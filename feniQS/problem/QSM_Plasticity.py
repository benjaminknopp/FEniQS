"""
QSM:
    Quasi Static Model
"""
from feniQS.problem.model_time_varying import *
from feniQS.problem.problem_plastic import *
from feniQS.problem.post_process import *
from feniQS.general.yaml_functions import *

pth_QSM_Plasticity = CollectPaths('./feniQS/problem/QSM_Plasticity.py')
pth_QSM_Plasticity.add_script(pth_model_time_varying)
pth_QSM_Plasticity.add_script(pth_problem_plastic)
pth_QSM_Plasticity.add_script(pth_post_process)
pth_QSM_Plasticity.add_script(pth_yaml_functions)

def get_QSM_Plasticity(pars_struct, cls_struct, pars_plasticity, _path=None, _name=None):
    struct = cls_struct(pars=pars_struct, _path=_path, _name=_name)
    return QSModelPlasticity(pars=pars_plasticity, struct=struct, _path=_path, _name=_name)

class QSModelPlasticity(QuasiStaticModel):
    def __init__(self, pars, struct, _path=None, _name=None \
                 , penalty_dofs=[], penalty_weight=0.):
        if isinstance(pars, dict):
            pars = ParsBase(**pars)
        ## The parameters of model might include structure parameters, thus, it must be updated accordingly.
        if struct.pars is not None:
            for kp in pars.__dict__.keys():
                if kp in struct.pars.__dict__.keys():
                    pars.__dict__[kp] = struct.pars.__dict__[kp]
        if _name is None:
            _name = 'QsPlasticity_' + struct._name
        self.struct = struct
        if not pars.mat_type.lower()=='plasticity':
            raise ValueError('The material type must be plasticity.')
        self.penalty_dofs = penalty_dofs
        self.penalty_weight = df.Constant(penalty_weight)
        QuasiStaticModel.__init__(self, pars, pars.softenned_pars, _path, _name)
    
    def establish_model(self): # over-written
        if self.pars._write_files:
            _n = 'model' if self._name=='' else self._name
            write_to_xdmf(self.struct.mesh, xdmf_name=_n+'_mesh.xdmf', xdmf_path=self._path)
        
        ### MATERIAL ###
        if self.pars.yield_surf['type'].lower()=='von-mises':
            sig0 = self.pars.yield_surf['pars']['sig0']
            H = self.pars.hardening_isotropic['modulus']
            yf = Yield_VM(sig0, constraint=self.pars.constraint, H=H)
            hhp = self.pars.hardening_isotropic['hypothesis']
            if H == 0: ## perfect plasticity (No hardening)
                mat = PlasticConsitutivePerfect(E=self.pars.E+self.pars.E_min \
                                                , nu=self.pars.nu, constraint=self.pars.constraint, yf=yf)
            else: ## Isotropic-hardenning plasticity
                if hhp.lower() == 'unit':
                    ri = RateIndependentHistory() # p = 1, i.e.: kappa_dot = lamda_dot
                elif hhp.lower() == 'plastic-work':
                    ri = RateIndependentHistory_PlasticWork(yf) # kappa_dot = plastic work rate
                else:
                    raise ValueError(f"The provided isotropic hardening hypothesis '{hhp}' is not recognized/implemented.")
                mat = PlasticConsitutiveRateIndependentHistory(E=self.pars.E+self.pars.E_min \
                                                               , nu=self.pars.nu, constraint=self.pars.constraint, yf=yf, ri=ri)
        else:
            raise NotImplementedError(f"The plasticity model for the given yield surface type '{self.pars.yield_surf['type']}' is not implemented.")

        ### PROBLEM ###
        self.fen = FenicsPlastic(mat, self.struct.mesh, fen_config=self.pars \
                                 , dep_dim=self.struct.mesh.geometric_dimension())
        # build variationals
        try:
            sigma_scale = self.struct.special_fenics_fields['sigma_scale']
        except KeyError:
            sigma_scale = 1.0
        self.fen.bcs_NM_tractions, self.fen.bcs_NM_measures = self.struct.get_tractions_and_dolfin_measures()
        self.fen.concentrated_forces = self.struct.get_concentrated_forces()
        self.time_varying_loadings = {}
        for direction, cfs in self.fen.concentrated_forces.items():
            for i, cf in enumerate(cfs):
                ff = cf['value']
                if (isinstance(ff, df.Expression) or isinstance(ff, df.UserExpression)) and hasattr(ff, 't'):
                    self.time_varying_loadings.update({f"f_concentrated_{direction}_{i}": ff})
        self.fen.build_variational_functionals(f=self.pars.f, integ_degree=self.pars.integ_degree, expr_sigma_scale=sigma_scale)
        self.fen.bcs_NM_dofs = self.struct.get_tractions_dofs(i_full=self.fen.get_i_full(), i_u=self.fen.get_iu())
        
        ### POST-PROCESS (default: regardless of solve_options) ###
        self.pps_default = {} # By default, no postprocessor (e.g. to compute/store residuals, for analytical computation of Jacobian)
        
        self.established = True
        
        self._commit_struct_to_model()
    
    def build_solver(self, solve_options):
        ### BUILD SOLVER ###
        tvls = list(self.time_varying_loadings.values()) # The order does not matter for adjusting 't' of time_varying_loadings
        self.fen.build_solver(solver_options=solve_options.solver_options, time_varying_loadings=tvls)
        self.solver_built = True
        
    def set_pps(self, solve_options, DG_degree=1):
        self.pps = []
        reaction_dofs = self.get_reaction_dofs(reaction_places=solve_options.reaction_places)
        _n = 'model' if self._name=='' else self._name
        self.pps.append(PostProcessPlastic(self.fen, _n, self._path \
                        , reaction_dofs=reaction_dofs, write_files=self.pars._write_files, DG_degree=DG_degree))
        for pp in self.pps_default.values():
            self.pps.append(pp)
    
    def solve(self, solve_options, other_pps=[], _reset_pps=True):
        ts, iterations, solve_options = QuasiStaticModel.solve(self, solve_options, other_pps, _reset_pps)
        return ts, iterations
    
    def _commit_struct_to_model(self):
        """
        To commit BCs and time-varying loadings from structure to the model.
        """
        bcs_hom, bcs_inhom, time_varying_loadings = self.struct.get_BCs(self.fen.get_iu())
        self.time_varying_loadings.update(time_varying_loadings)
        bcs_hom = [bc['bc'] for bc in bcs_hom.values()]
        bcs_inhom = [bc['bc'] for bc in bcs_inhom.values()]
        self.revise_BCs(remove=True, new_BCs=bcs_hom, _as='hom')
        self.revise_BCs(remove=False, new_BCs=bcs_inhom, _as='inhom')
    
    def get_reaction_dofs(self, reaction_places):
        return self.struct.get_reaction_dofs(reaction_places, i_u=self.fen.get_iu())
