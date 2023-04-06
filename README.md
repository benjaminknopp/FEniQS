# FEniQS
A library for simulating structural mechanics problems in FEniCS, with several main modules:
  ## structure:
  Dealing with the definition of a structural mechanics problem, including geometry, mesh, boundary conditions (BCs), and time-varying loadings.
  ## material:
  Handling constitutive laws such as elasticity, gradient damage, plasticity, etc.
  ## problem:
  Combining the two modules above, building respective structural mechanics problems, and solving them. These can be performed for two main cases: static (including homogenization), and quasi-static (abbreviated as QS).
