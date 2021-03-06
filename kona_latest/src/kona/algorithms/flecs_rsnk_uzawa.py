from kona.algorithms.base_algorithm import OptimizationAlgorithm
# from kona.linalg.matrices.preconds import UZAWA
from kona.linalg.matrices.preconds import IterSolver
from kona.linalg.solvers.krylov import FGMRES


class FLECS_RSNK(OptimizationAlgorithm):
    """
    A reduced-space Newton-Krylov optimization algorithm for PDE-governed
    (in)equality constrained problems.

    This algorithm uses a novel 2nd order adjoint formulation of the KKT
    matrix-vector product, in conjunction with a novel Krylov-method called
    FLECS for non-convex saddle point problems.

    Inequality constraints are converted to equality constraints using slack
    terms of the form :math:`e^s` where `s` are the slack variables.

    The KKT system is then preconditioned using a nested solver operating on
    an approximation of the KKT matrix-vector product. This approximation is
    assembled using the PDE preconditioner on 2nd order adjoing solves.

    The step produced by FLECS is globalized using a trust region approach.

    .. note::

        More information on this reduced-space Newton-Krylov appoach can be
        found `in this paper <http://arc.aiaa.org/doi/abs/10.2514/6.2015-1945>`.

    Parameters
    ----------
    primal_factory : VectorFactory
    state_factory : VectorFactory
    dual_factory : VectorFactory
    optns : dict, optional
    """
    def __init__(self, primal_factory, state_factory,
                 eq_factory, ineq_factory, optns=None):
        # trigger base class initialization
        super(FLECS_RSNK, self).__init__(
            primal_factory, state_factory, eq_factory, ineq_factory, optns
        )

        # number of vectors required in solve() method
        self.primal_factory.request_num_vectors(6 + 1)
        self.state_factory.request_num_vectors(3)
        self.eq_factory.request_num_vectors(12 + 2)

        # general options
        ############################################################
        self.factor_matrices = get_opt(self.optns, False, 'matrix_explicit')

        # trust radius settings
        ############################################################
        self.radius = get_opt(self.optns, 0.5, 'trust', 'init_radius')
        self.min_radius = get_opt(self.optns, 0.5/(2**3), 'trust', 'min_radius')
        self.max_radius = get_opt(self.optns, 0.5*(2**3), 'trust', 'max_radius')

        # augmented Lagrangian settings
        ############################################################
        self.mu = get_opt(self.optns, 1.0, 'penalty', 'mu_init')
        self.mu_init = self.mu
        self.mu_pow = get_opt(self.optns, 0.5, 'penalty', 'mu_pow')
        self.mu_max = get_opt(self.optns, 1e5, 'penalty', 'mu_max')
        self.eta = 1./(self.mu**0.1)

        # reduced KKT settings
        ############################################################
        self.nu = get_opt(self.optns, 0.95, 'rsnk', 'nu')
        reduced_optns = get_opt(self.optns, {}, 'rsnk')
        reduced_optns['out_file'] = self.info_file
        self.KKT_matrix = ReducedKKTMatrix(
            [self.primal_factory, self.state_factory,
             self.eq_factory],
            reduced_optns)
        self.mat_vec = self.KKT_matrix.product

        # KKT system preconditiner settings
        ############################################################
        self.precond = get_opt(self.optns, None, 'rsnk', 'precond')
        self.idf_schur = None

        self.nested = None
        self.uzawa = None
        self.itersolver = None

        if self.precond is None:
            # use identity matrix product as preconditioner
            self.eye = IdentityMatrix()
            self.precond = self.eye.product
        elif self.precond is 'idf_schur':
            self.idf_schur = ReducedSchurPreconditioner(
                [primal_factory, state_factory, eq_factory, ineq_factory])
            self.precond = self.idf_schur.product

        elif self.precond == 'uzawa':
            self.primal_factory.request_num_vectors(2) # clean this up!! 
            self.dual_factory.request_num_vectors(5)
            uzawa_optns = get_opt(reduced_optns, {}, 'uzawa')

            self.uzawa = UZAWA(
                [self.primal_factory, self.state_factory, self.dual_factory], uzawa_optns)
            self.precond = self.uzawa.solve
        
        elif self.precond == 'iter_solver':
            self.itersolver = IterSolver(
                [self.primal_factory, self.state_factory, self.dual_factory])
            self.precond = self.itersolver.solve

        else:
            raise BadKonaOption(self.optns, 'rsnk', 'precond')

        # krylov solver settings
        ############################################################
        krylov_optns = {
            'krylov_file'   : get_opt(
                self.optns, 'kona_krylov.dat', 'rsnk', 'krylov_file'),
            'subspace_size' : get_opt(self.optns, 10, 'rsnk', 'subspace_size'),
            'check_res'     : get_opt(self.optns, True, 'rsnk', 'check_res'),
            'rel_tol'       : get_opt(self.optns, 1e-2, 'rsnk', 'rel_tol'),
        }
        self.krylov = FLECS(
            [self.primal_factory, self.eq_factory],
            krylov_optns)

        self.krylov_fgmres = FGMRES(self.primal_factory, krylov_optns, self.dual_factory)

        # get globalization options
        ############################################################
        self.globalization = get_opt(self.optns, 'trust', 'globalization')
        if self.globalization is None:
            self.trust_region = False
        elif self.globalization == 'trust':
            self.trust_region = True
        else:
            raise TypeError(
                'Invalid globalization! ' +
                'Can only use \'trust\'. ' +
                'If you want to skip globalization, set to None.')

    def _write_header(self):
        self.hist_file.write(
            '# Kona constrained RSNK convergence history file\n' +
            '# iters' + ' '*5 +
            '   cost' + ' '*5 +
            'optimality  ' + ' '*5 +
            'design_opt  ' + ' '*5 +
            'slack_opt   ' + ' '*5 +
            'feasibility ' + ' '*5 +
            'objective   ' + ' '*5 +
            'mu param    ' + ' '*5 +
            'radius      ' + '\n'
        )

    def _write_history(self, opt, x_opt, s_opt, feas, obj):
        self.hist_file.write(
            '%7i'%self.iter + ' '*5 +
            '%7i'%self.primal_factory._memory.cost + ' '*5 +
            '%11e'%opt + ' '*5 +
            '%11e'%x_opt + ' '*5 +
            '%11e'%s_opt + ' '*5 +
            '%11e'%feas + ' '*5 +
            '%11e'%obj + ' '*5 +
            '%11e'%self.mu + ' '*5 +
            '%11e'%self.radius + '\n'
        )

    def _generate_KKT_vector(self):
        primal = self.primal_factory.generate()
        dual = self.eq_factory.generate()
        return ReducedKKTVector(primal, dual)

    def _generate_primal_vector(self):
        design = self.primal_factory.generate()
        slack = self.dual_factory.generate()
        return CompositePrimalVector(design, slack)

    def _generate_primal_vector(self):
        design = self.primal_factory.generate()
        slack = self.dual_factory.generate()
        return CompositePrimalVector(design, slack)

    def solve(self):
        self._write_header()
        self.info_file.write(
            '\n' +
            '**************************************************\n' +
            '***        Using FLECS-based Algorithm         ***\n' +
            '**************************************************\n' +
            '\n')

        # generate composite KKT vectors
        X = self._generate_KKT_vector()
        X_work = self._generate_KKT_vector()
        P = self._generate_KKT_vector()
        dLdX = self._generate_KKT_vector()
        kkt_rhs = self._generate_KKT_vector()
        kkt_save = self._generate_KKT_vector()
        kkt_work = self._generate_KKT_vector()

        # generate primal vectors
        primal_work = self.primal_factory.generate()

        # generate state vectors
        state = self.state_factory.generate()
        state_work = self.state_factory.generate()
        adjoint = self.state_factory.generate()

        # generate dual vectors
        dual_work = self.eq_factory.generate()

        # some extra vectors for Uzawa BFGS part
        if self.uzawa is not None:
            X_olddual = self._generate_KKT_vector()
            dLdX_olddual = self._generate_KKT_vector()
            old_dual = self.dual_factory.generate()
                     
        # initialize basic data for outer iterations
        converged = False
        self.iter = 0

        # evaluate the initial design before starting outer iterations
        X.equals_init_guess()
        state.equals_primal_solution(X.primal)
        if self.factor_matrices and self.iter < self.max_iter:
            factor_linear_system(X.primal, state)

        # perform an adjoint solution for the Lagrangian
        state_work.equals_objective_partial(X.primal, state)
        dCdU(X.primal, state).T.product(X.dual, adjoint)
        state_work.plus(adjoint)
        state_work.times(-1.)
        dRdU(X.primal, state).T.solve(state_work, adjoint)

        # send initial point info to the user
        solver_info = current_solution(self.iter, X.primal, state, adjoint,
                                       X.dual)
        if isinstance(solver_info, str):
            self.info_file.write('\n' + solver_info + '\n')

        # BEGIN NEWTON LOOP HERE
        ###############################
        min_radius_active = False
        for i in xrange(self.max_iter):
            # advance iteration counter
            self.iter += 1

            # evaluate optimality, feasibility and KKT norms
            dLdX.equals_KKT_conditions(X, state, adjoint)
            # print info on current point
            self.info_file.write(
                '==========================================================\n' +
                'Beginning Major Iteration %i\n\n'%self.iter)
            self.info_file.write(
                'primal vars        = %e\n'%X.primal.norm2)
            self.info_file.write(
                'multipliers        = %e\n\n'%X.dual.norm2)

            if self.iter == 1:
                # calculate initial norms
                self.grad_norm0 = dLdX.primal.norm2
                self.feas_norm0 = max(dLdX.dual.norm2, EPS)
                self.kkt_norm0 = np.sqrt(
                    self.feas_norm0**2 + self.grad_norm0**2)

                # set current norms to initial
                kkt_norm = self.kkt_norm0
                grad_norm = self.grad_norm0
                feas_norm = self.feas_norm0

                # print out convergence norms
                self.info_file.write(
                    'grad_norm0         = %e\n'%self.grad_norm0 +
                    'feas_norm0         = %e\n'%self.feas_norm0)

                # calculate convergence tolerances
                grad_tol = self.primal_tol * max(self.grad_norm0, 1e-3)
                feas_tol = self.cnstr_tol * max(self.feas_norm0, 1e-3)

            else:
                # calculate current norms
                grad_norm = dLdX.primal.norm2
                feas_norm = max(dLdX.dual.norm2, EPS)
                kkt_norm = np.sqrt(feas_norm**2 + grad_norm**2)

                # update the augmented Lagrangian penalty
                self.info_file.write(
                    'grad_norm          = %e (%e <-- tolerance)\n'%(
                        grad_norm, grad_tol) +
                    'feas_norm          = %e (%e <-- tolerance)\n'%(
                        feas_norm, feas_tol))

            # update penalty term
            ref_norm = min(grad_norm, feas_norm)
            self.mu = max(
                self.mu,
                self.mu_init * ((self.feas_norm0/ref_norm)**self.mu_pow))
            self.mu = min(self.mu, self.mu_max)

            # write convergence history
            obj_val = objective_value(X._primal._design, state)
            self._write_history(grad_norm, design_norm, slack_norm, feas_norm, obj_val)

            # check for convergence
            if (grad_norm < grad_tol) and (feas_norm < feas_tol):
                converged = True
                break

            # compute krylov tolerances in order to achieve superlinear
            # convergence but to avoid oversolving
            krylov_tol = self.krylov.rel_tol*min(
                1.0, np.sqrt(kkt_norm/self.kkt_norm0))
            krylov_tol = max(krylov_tol,
                             min(grad_tol/grad_norm,
                                 feas_tol/feas_norm))
            krylov_tol *= self.nu

            # set ReducedKKTMatrix product tolerances
            if self.KKT_matrix.dynamic_tol:
                raise NotImplementedError(
                    'ConstrainedRSNK.solve()' +
                    'not yet set up for dynamic tolerance in product')
            else:
                self.KKT_matrix.product_fac *= \
                    krylov_tol/self.krylov.max_iter

            # set other solver and product options
            self.KKT_matrix.lamb = 0.0
            self.krylov.rel_tol = krylov_tol
            self.krylov.radius = self.radius
            self.krylov.mu = self.mu

            # linearize the KKT matrix
            self.KKT_matrix.linearize(X, state, adjoint)
            if self.idf_schur is not None:
                self.idf_schur.linearize(X, state)

            if self.uzawa is not None:
                if self.iter == 1:
                    self.uzawa.linearize(
                        X, state, adjoint, dLdX, dLdX)                    
                else:
                    X_olddual.equals(X)
                    X_olddual._dual.equals(old_dual)
                    dLdX_olddual.equals_KKT_conditions(
                    X_olddual, state, adjoint, primal_work, dual_work)

                    self.uzawa.linearize(X, state, adjoint, dLdX, dLdX_olddual)
                    
                old_dual.equals(X._dual)

            if self.itersolver is not None:
                self.itersolver.linearize(X, state)

            # move the vector to the RHS
            kkt_rhs.equals(dLdX)
            kkt_rhs.times(-1.)

            # reset the primal-dual step vector
            P.equals(0.0)

            # if self.iter < 20:
            #     print 'IDENTITY MATRIX FOR PRECOND'
            #     self.eye = IdentityMatrix()
            #     self.precond = self.eye.product
            # #     # self.krylov.solve(self.mat_vec, kkt_rhs, P, self.precond)
            # #     # self.radius = self.krylov.radius
            # else:
            #     print 'ITERSOLVER PRECOND'
            #     self.precond = self.itersolver.solve
            # #     # self.trust_region = None
            #     self.krylov.max_iter=40
            # #     # self.mu = 20
            # #     # self.mu_pow = 0.4


                # self.krylov_fgmres.solve(self.mat_vec, kkt_rhs, P, self.precond)
                # self.krylov.solve(self.mat_vec, kkt_rhs, P, self.precond)
                # self.radius = self.krylov.radius

                # P._primal._slack.divide_by( P._primal._slack.norm2 )
                # print 'fgmres P._primal._slack.norm2', P._primal._slack.norm2

            # trigger the krylov solution
            self.krylov.solve(self.mat_vec, kkt_rhs, P, self.precond)
            self.radius = self.krylov.radius

            print 'At ITER', self.iter
            print 'P._primal._design.norm2', P._primal._design.norm2
            print 'P._primal._slack.norm2', P._primal._slack.norm2
            print 'P._dual.norm2', P._dual.norm2
            


            #----------------- add extra ended ---------------
            # if self.iter >= 20:
            #     X_work.equals(X)
            #     X_work._primal.plus(P._primal)
            #     X_work._primal._design.enforce_bounds()
            #     X_work._primal._slack.restrict()
            #     X_work._dual.plus(P._dual)

            #     state_work.equals_primal_solution(X_work._primal._design)
            #     dual_work.equals_constraints(X_work._primal._design, state_work)
                
            #     lower_limit = 1e-6*np.ones_like(X_work._dual._data.x_lower.x)

            #     slack_lower_limit = np.log(np.maximum(dual_work._data.x_lower.x, lower_limit))
            #     slack_upper_limit = np.log(np.maximum(dual_work._data.x_upper.x, lower_limit))
            #     slack_stress_limit = np.log(np.maximum(dual_work._data.stress.x, lower_limit))

            #     lower_logic = np.less_equal( np.absolute(X_work._primal._slack._data.x_lower.x ),  np.absolute( slack_lower_limit ) )
            #     lower_logic_not = np.logical_not(lower_logic)
            #     upper_logic = np.less_equal( np.absolute(X_work._primal._slack._data.x_upper.x ),  np.absolute( slack_upper_limit ) )
            #     upper_logic_not = np.logical_not(upper_logic)
            #     stress_logic = np.less_equal( np.absolute(X_work._primal._slack._data.stress.x ),  np.absolute( slack_stress_limit ) )
            #     stress_logic_not = np.logical_not(stress_logic)
            #     if any(lower_logic_not):
            #         print 'slack_lower_limit used in one element at least'
            #     if any(upper_logic_not):
            #         print 'slack_upper_limit used in one element at least'
            #     if any(stress_logic_not):
            #         print 'slack_stress_limit used in one element at least'
            #     # import pdb; pdb.set_trace()
            #     X_work._primal._slack._data.x_lower.x = lower_logic*X_work._primal._slack._data.x_lower.x  +  lower_logic_not*slack_lower_limit
            #     X_work._primal._slack._data.x_upper.x = upper_logic*X_work._primal._slack._data.x_upper.x  +  upper_logic_not*slack_upper_limit
            #     X_work._primal._slack._data.stress.x =  stress_logic*X_work._primal._slack._data.stress.x  + stress_logic_not*slack_stress_limit

            #     # X_work._primal._slack._data.x_lower.x = slack_lower_limit
            #     # X_work._primal._slack._data.x_upper.x = slack_upper_limit
            #     # X_work._primal._slack._data.stress.x =   slack_stress_limit

            #     P._primal._slack.equals(X_work._primal._slack)
            #     P._primal._slack.minus(X._primal._slack)

            #     print 'After INTERVETION', 
            #     # print 'P._primal._design.norm2', P._primal._design.norm2
            #     print 'P._primal._slack.norm2', P._primal._slack.norm2
            #     print 'P._dual.norm2', P._dual.norm2

            #-----------------e n d e d-----------



            # apply globalization
            if self.trust_region:
                old_flag = min_radius_active
                success, min_radius_active = self.trust_step(
                    X, state, adjoint, P, kkt_rhs, krylov_tol, feas_tol,
                    primal_work, state_work, dual_work,
                    kkt_work, kkt_save)

                # #----------------- add extra ended ---------------
                # if self.iter >= 20:
                #     state_work.equals_primal_solution(X._primal._design)
                #     dual_work.equals_constraints(X._primal._design, state_work)
                    
                #     lower_limit = 1e-6*np.ones_like(X._dual._data.x_lower.x)

                #     slack_lower_limit = np.log(np.maximum(dual_work._data.x_lower.x, lower_limit))
                #     slack_upper_limit = np.log(np.maximum(dual_work._data.x_upper.x, lower_limit))
                #     slack_stress_limit = np.log(np.maximum(dual_work._data.stress.x, lower_limit))

                #     lower_logic = np.less_equal( np.absolute(X._primal._slack._data.x_lower.x ),  np.absolute( slack_lower_limit ) )
                #     lower_logic_not = np.logical_not(lower_logic)
                #     upper_logic = np.less_equal( np.absolute(X._primal._slack._data.x_upper.x ),  np.absolute( slack_upper_limit ) )
                #     upper_logic_not = np.logical_not(upper_logic)
                #     stress_logic = np.less_equal( np.absolute(X._primal._slack._data.stress.x ),  np.absolute( slack_stress_limit ) )
                #     stress_logic_not = np.logical_not(stress_logic)
                #     if any(lower_logic_not):
                #         print 'slack_lower_limit used in one element at least'
                #     if any(upper_logic_not):
                #         print 'slack_upper_limit used in one element at least'
                #     if any(stress_logic_not):
                #         print 'slack_stress_limit used in one element at least'
                #     # import pdb; pdb.set_trace()
                #     X._primal._slack._data.x_lower.x = lower_logic*X._primal._slack._data.x_lower.x  +  lower_logic_not*slack_lower_limit
                #     X._primal._slack._data.x_upper.x = upper_logic*X._primal._slack._data.x_upper.x  +  upper_logic_not*slack_upper_limit
                #     X._primal._slack._data.stress.x =  stress_logic*X._primal._slack._data.stress.x  + stress_logic_not*slack_stress_limit

                #     # X._primal._slack._data.x_lower.x = slack_lower_limit
                #     # X._primal._slack._data.x_upper.x = slack_upper_limit
                #     # X._primal._slack._data.stress.x =   slack_stress_limit
                #     #-----------------e n d e d-----------


                # watchdog on trust region failures
                if min_radius_active and old_flag:
                    self.info_file.write(
                        'Trust radius breakdown! Terminating...\n')
                    break
            else:
                # accept step
                X.primal.plus(P.primal)
                X.dual.plus(P.dual)

                # calculate states
                state.equals_primal_solution(X.primal)


                # #---------- add extra for enforcing bounds on slack vars ---------
                # state_work.equals_primal_solution(X._primal._design)
                # dual_work.equals_constraints(X._primal._design, state_work)
                
                # lower_limit = 1e-6*np.ones_like(dual_work._data.x_lower.x)

                # slack_lower_limit = np.log(np.maximum(dual_work._data.x_lower.x, lower_limit))
                # slack_upper_limit = np.log(np.maximum(dual_work._data.x_upper.x, lower_limit))
                # slack_stress_limit = np.log(np.maximum(dual_work._data.stress.x, lower_limit))

                # # X._primal._slack._data.x_lower.x = np.minimum(slack_lower_limit, X._primal._slack._data.x_lower.x )
                # # X._primal._slack._data.x_upper.x = np.minimum(slack_upper_limit, X._primal._slack._data.x_upper.x )
                # # X._primal._slack._data.stress.x = np.minimum(slack_stress_limit, X._primal._slack._data.stress.x )

                # X._primal._slack._data.x_lower.x = slack_lower_limit
                # X._primal._slack._data.x_upper.x = slack_upper_limit
                # X._primal._slack._data.stress.x = slack_stress_limit


                # print 'max(X._primal._slack._data.x_lower.x)', max(X._primal._slack._data.x_lower.x)
                # print 'max(X._primal._slack._data.x_upper.x)', max(X._primal._slack._data.x_upper.x)
                # print 'max(X._primal._slack._data.stress.x)', max(X._primal._slack._data.stress.x)

                #----------------- add extra ended ---------------




                # #---------- add extra for enforcing bounds on slack vars ---------
                # state_work.equals_primal_solution(X._primal._design)
                # dual_work.equals_constraints(X._primal._design, state_work)
                
                # lower_limit = 1e-6*np.ones_like(dual_work._data.x_lower.x)

                # slack_lower_limit = np.log(np.maximum(dual_work._data.x_lower.x, lower_limit))
                # slack_upper_limit = np.log(np.maximum(dual_work._data.x_upper.x, lower_limit))
                # slack_stress_limit = np.log(np.maximum(dual_work._data.stress.x, lower_limit))

                # # X._primal._slack._data.x_lower.x = np.minimum(slack_lower_limit, X._primal._slack._data.x_lower.x )
                # # X._primal._slack._data.x_upper.x = np.minimum(slack_upper_limit, X._primal._slack._data.x_upper.x )
                # # X._primal._slack._data.stress.x = np.minimum(slack_stress_limit, X._primal._slack._data.stress.x )

                # X._primal._slack._data.x_lower.x = slack_lower_limit
                # X._primal._slack._data.x_upper.x = slack_upper_limit
                # X._primal._slack._data.stress.x = slack_stress_limit


                # print 'max(X._primal._slack._data.x_lower.x)', max(X._primal._slack._data.x_lower.x)
                # print 'max(X._primal._slack._data.x_upper.x)', max(X._primal._slack._data.x_upper.x)
                # print 'max(X._primal._slack._data.stress.x)', max(X._primal._slack._data.stress.x)

                #----------------- add extra ended ---------------



                # if this is a matrix-based problem, tell the solver to factor
                # some important matrices to be used in the next iteration
                if self.factor_matrices and self.iter < self.max_iter:
                    factor_linear_system(X.primal, state)

                # perform an adjoint solution for the Lagrangian
                state_work.equals_objective_partial(X.primal, state)
                dCdU(X.primal, state).T.product(X.dual, adjoint)
                state_work.plus(adjoint)
                state_work.times(-1.)
                dRdU(X.primal, state).T.solve(state_work, adjoint)

            # send current solution info to the user
            solver_info = current_solution(
                X._primal._design, state, adjoint, X._dual, self.iter,
                X._primal._slack)  
 
            if isinstance(solver_info, str):
                self.info_file.write('\n' + solver_info + '\n')
            # import pdb; pdb.set_trace()
        ############################
        # END OF NEWTON LOOP

        if converged:
            self.info_file.write('Optimization successful!\n')
        else:
            self.info_file.write('Optimization FAILED!\n')

        self.info_file.write(
            'Total number of nonlinear iterations: %i\n\n'%self.iter)

    def trust_step(self, X, state, adjoint, P, kkt_rhs, krylov_tol, feas_tol,
                   primal_work, state_work, dual_work, kkt_work, kkt_save):
        # start trust region loop
        max_iter = 6
        iters = 0
        min_radius_active = False
        converged = False
        self.info_file.write('\n')
        while iters <= max_iter:
            iters += 1
            # evaluate the constraint term at the current step
            dual_work.equals_constraints(X.primal, state)
            # compute the merit value at the current step
            # print 'HAS IT COME TO HERE? 2'
            merit_init = objective_value(X._primal._design, state) \
                + X._dual.inner(dual_work) \
                + 0.5*self.mu*(dual_work.norm2**2)
            # add the FLECS step
            kkt_work.equals_ax_p_by(1., X, 1., P)
            kkt_work._primal._design.enforce_bounds()
            # print 'HAS IT COME TO HERE? 3'

            # #---------- add extra for enforcing bounds on slack vars ---------
            # if self.iter >= 20:
            #     state_work.equals_primal_solution(kkt_work._primal._design)
            #     dual_work.equals_constraints(kkt_work._primal._design, state_work)
                
            #     lower_limit = 1e-6*np.ones_like(dual_work._data.x_lower.x)

            #     slack_lower_limit = np.log(np.maximum(dual_work._data.x_lower.x, lower_limit))
            #     slack_upper_limit = np.log(np.maximum(dual_work._data.x_upper.x, lower_limit))
            #     slack_stress_limit = np.log(np.maximum(dual_work._data.stress.x, lower_limit))

            #     lower_logic = np.less_equal( np.absolute(kkt_work._primal._slack._data.x_lower.x ),  np.absolute( slack_lower_limit ) )
            #     lower_logic_not = np.logical_not(lower_logic)
            #     upper_logic = np.less_equal( np.absolute(kkt_work._primal._slack._data.x_upper.x ),  np.absolute( slack_upper_limit ) )
            #     upper_logic_not = np.logical_not(upper_logic)
            #     stress_logic = np.less_equal( np.absolute(kkt_work._primal._slack._data.stress.x ),  np.absolute( slack_stress_limit ) )
            #     stress_logic_not = np.logical_not(stress_logic)
            #     if any(lower_logic_not):
            #         print 'slack_lower_limit used in one element at least'
            #     if any(upper_logic_not):
            #         print 'slack_upper_limit used in one element at least'
            #     if any(stress_logic_not):
            #         print 'slack_stress_limit used in one element at least'
            #     # import pdb; pdb.set_trace()
            #     kkt_work._primal._slack._data.x_lower.x = lower_logic*kkt_work._primal._slack._data.x_lower.x  +  lower_logic_not*slack_lower_limit
            #     kkt_work._primal._slack._data.x_upper.x = upper_logic*kkt_work._primal._slack._data.x_upper.x  +  upper_logic_not*slack_upper_limit
            #     kkt_work._primal._slack._data.stress.x =  stress_logic*kkt_work._primal._slack._data.stress.x  + stress_logic_not*slack_stress_limit
            #     # # import pdb; pdb.set_trace()

            # ##----------------- add extra ended ---------------

            kkt_work._primal._slack.restrict()
            # print 'HAS IT COME TO HERE? 4'
            # solve states at the new step
            if state_work.equals_primal_solution(kkt_work.primal):
                # evaluate the constraint terms at the new step
                dual_work.equals_constraints(
                    kkt_work._primal._design, state_work)
                # print 'HAS IT COME TO HERE? 5'
                slack_work.exp(kkt_work._primal._slack)
                # print 'HAS IT COME TO HERE? 6'
                slack_work.times(-1.)
                slack_work.restrict()
                dual_work.plus(slack_work)

                # compute the merit value at the next step
                merit_next = objective_value(kkt_work.primal, state) \
                    + X.dual.inner(dual_work) \
                    + 0.5*self.mu*(dual_work.norm2**2)
                # evaluate the quality of the FLECS model
                rho = (merit_init - merit_next)/self.krylov.pred_aug
            else:
                merit_next = np.nan
                rho = np.nan

            self.info_file.write(
                'Trust Region Step : iter %i\n'%iters +
                '   primal_step    = %e\n'%P.primal.norm2 +
                '   lambda_step    = %e\n'%P.dual.norm2 +
                '\n' +
                '   merit_init     = %e\n'%merit_init +
                '   merit_next     = %e\n'%merit_next +
                '   pred_aug       = %e\n'%self.krylov.pred_aug +
                '   rho            = %e\n'%rho)

            # modify radius based on model quality
            if rho <= 0. or np.isnan(rho):
                # model is bad! -- first we try a 2nd order correction
                if iters == 1:
                    # save the old step in case correction fails
                    kkt_save.equals(P)
                    # attempt a 2nd order correction
                    self.info_file.write(
                        '   Attempting a second order correction...\n')
                    self.krylov.apply_correction(dual_work, P)
                elif iters == 2:
                    # if we got here, the second order correction failed
                    # reject step
                    self.info_file.write(
                        '   Correction failed! Resetting step...\n')
                    P.equals(kkt_save)
                else:
                    self.radius = max(0.5*self.radius, self.min_radius)
                    if self.radius == self.min_radius:
                        self.info_file.write(
                            '      Reached minimum radius! ' +
                            'Exiting globalization...\n')
                        min_radius_active = True
                        break
                    else:
                        self.info_file.write(
                            '   Re-solving with smaller radius -> ' +
                            '%f\n'%self.radius)
                        self.krylov.radius = self.radius
                        self.krylov.re_solve(kkt_rhs, P)
                        self.radius = self.krylov.radius
            else:
                if iters == 2:
                    # 2nd order correction worked -- yay!
                    self.info_file.write('   Correction worked!\n')

                # model is okay -- accept primal step
                self.info_file.write('\nStep accepted!\n')

                # accept the new step entirely
                X.plus(P)
                state.equals_primal_solution(X.primal)

                # if this is a matrix-based problem, tell the solver to factor
                # some important matrices to be used in the next iteration
                if self.factor_matrices and self.iter < self.max_iter:
                    factor_linear_system(X.primal, state)

                # perform an adjoint solution for the Lagrangian
                state_work.equals_objective_partial(X.primal, state)
                dCdU(X.primal, state).T.product(X.dual, adjoint)
                state_work.plus(adjoint)
                state_work.times(-1.)
                dRdU(X.primal, state).T.solve(state_work, adjoint)

                # check the trust radius
                if self.krylov.trust_active:
                    # if active, decide if we want to increase it
                    self.info_file.write('Trust radius active...\n')
                    if rho >= 0.5:
                        # model is good enough -- increase radius
                        self.radius = min(2.*P.primal.norm2, self.max_radius)
                        # self.radius = min(2.*self.radius, self.max_radius)
                        self.info_file.write(
                            '   Radius increased -> %f\n'%self.radius)
                        min_radius_active = False

                # trust radius globalization worked, break loop
                converged = True
                self.info_file.write('\n')
                break

        return converged, min_radius_active

# imports here to prevent circular errors
import numpy as np
from kona.options import BadKonaOption, get_opt
from kona.linalg.common import current_solution, objective_value
from kona.linalg.common import factor_linear_system
from kona.linalg.vectors.composite import ReducedKKTVector
from kona.linalg.matrices.common import dCdU, dRdU, IdentityMatrix
from kona.linalg.matrices.hessian import ReducedKKTMatrix
from kona.linalg.matrices.preconds import ReducedSchurPreconditioner
from kona.linalg.solvers.krylov import FLECS
from kona.linalg.solvers.util import EPS
