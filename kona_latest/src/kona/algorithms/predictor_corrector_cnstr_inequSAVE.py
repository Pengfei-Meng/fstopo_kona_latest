from kona.algorithms.base_algorithm import OptimizationAlgorithm
import pdb, pickle
class PredictorCorrectorCnstrINEQ(OptimizationAlgorithm):

    def __init__(self, primal_factory, state_factory,
                 eq_factory, ineq_factory, optns=None):
        # trigger base class initialization
        super(PredictorCorrectorCnstrINEQ, self).__init__(
            primal_factory, state_factory, eq_factory, ineq_factory, optns
        )

        # number of vectors required in solve() method
        self.primal_factory.request_num_vectors(13)
        self.state_factory.request_num_vectors(5)

        if self.eq_factory is not None:
            self.eq_factory.request_num_vectors(13)

        if self.ineq_factory is not None:
            self.ineq_factory.request_num_vectors(50)

        # general options
        ############################################################
        self.factor_matrices = get_opt(self.optns, False, 'matrix_explicit')

        # reduced hessian settings
        ############################################################
        self.hessian = ReducedKKTMatrix(
            [self.primal_factory, self.state_factory, self.eq_factory, self.ineq_factory])
        self.mat_vec = self.hessian.product

        # hessian preconditiner settings
        ############################################################
        self.precond = get_opt(self.optns, None, 'rsnk', 'precond')
        self.idf_schur = None
        if self.precond is None:
            # use identity matrix product as preconditioner
            self.eye = IdentityMatrix()
            self.precond = self.eye.product
        # elif self.precond is 'idf_schur':
        #     self.idf_schur = ReducedSchurPreconditioner(
        #         [primal_factory, state_factory, eq_factory, ineq_factory])
        #     self.precond = self.idf_schur.product
        # else:
        #     raise BadKonaOption(self.optns, 'rsnk', 'precond')

        # krylov solver settings
        ############################################################
        krylov_optns = {
            'krylov_file'   : get_opt(
                self.optns, 'kona_krylov.dat', 'rsnk', 'krylov_file'),
            'subspace_size' : get_opt(self.optns, 10, 'rsnk', 'subspace_size'),
            'check_res'     : get_opt(self.optns, True, 'rsnk', 'check_res'),
            'rel_tol'       : get_opt(self.optns, 1e-2, 'rsnk', 'rel_tol'),
        }
        self.krylov = FGMRES(self.primal_factory, krylov_optns,
                             eq_factory=self.eq_factory, ineq_factory=self.ineq_factory)

        self.outdir = krylov_optns['krylov_file'].split('/')[1]
        # homotopy options
        ############################################################
        self.mu = get_opt(self.optns, 1.0, 'homotopy', 'init_homotopy_parameter')
        self.inner_tol = get_opt(self.optns, 1e-1, 'homotopy', 'inner_tol')
        self.inner_maxiter = get_opt(self.optns, 50, 'homotopy', 'inner_maxiter')
        self.step = get_opt(
            self.optns, 0.05, 'homotopy', 'init_step')
        self.nom_dcurve = get_opt(self.optns, 1.0, 'homotopy', 'nominal_dist')
        self.nom_angl = get_opt(
            self.optns, 5.0*np.pi/180., 'homotopy', 'nominal_angle')
        self.max_factor = get_opt(self.optns, 2.0, 'homotopy', 'max_factor')
        self.min_factor = get_opt(self.optns, 0.5, 'homotopy', 'min_factor')
        self.dmu_max = get_opt(self.optns, -0.1, 'homotopy', 'dmu_max')
        self.dmu_min = get_opt(self.optns, -0.9, 'homotopy', 'dmu_min')

        # print 'self.max_iter:', self.max_iter
        # print 'self.mu: ', self.mu
        # print 'self.inner_tol: ', self.inner_tol

    def _write_header(self, opt_tol, feas_tol):
        self.hist_file.write(
            '# Kona Param. Contn. convergence history ' +
            '(opt tol = %e | feas tol = %e)\n'%(opt_tol, feas_tol) +
            '# outer' + ' '*5 +
            'inner' + ' '*5 +
            ' cost' + ' '*5 +
            ' objective' + ' ' * 5 +
            'lagrangian' + ' ' * 5 +
            '  opt norm' + ' '*5 +
            ' feas norm' + ' '*5 +
            '  homotopy' + ' ' * 5 +
            '   hom opt' + ' '*5 +
            '  hom feas' + ' '*5 +
            'mu        ' + ' '*5 +
            # 'min(slack)' + ' '*5 +
            # 'max(lamda)' + ' '*5 +
            # 'ineq_norm2' + ' '*5 +
            '\n'
        )

    def _write_outer(self, outer, cost, obj, lag, opt_norm, feas_norm, mu):
        if obj < 0.:
            obj_fmt = '%.3e'%obj
        else:
            obj_fmt = ' %.3e'%obj
        if lag < 0.:
            lag_fmt = '%.3e'%lag
        else:
            lag_fmt = ' %.3e'%lag
        self.hist_file.write(
            '%7i' % outer + ' ' * 5 +
            '-'*5 + ' ' * 5 +
            '%5i' % cost + ' ' * 5 +
            obj_fmt + ' ' * 5 +
            lag_fmt + ' ' * 5 +
            '%.4e' % opt_norm + ' ' * 5 +
            '%.4e' % feas_norm + ' ' * 5 +
            '-'*10 + ' ' * 5 +
            '-'*10 + ' ' * 5 +
            '-'*10 + ' ' * 5 +
            '%1.4f' % mu + ' ' * 5 +
            # '-'*10 + ' ' * 5 +
            # '-'*10 + ' ' * 5 +
            # '-'*10 + ' ' * 5 +
            '\n'
        )

    def _write_inner(self, outer, inner,
                     obj, lag, opt_norm, feas_norm,
                     hom, hom_opt, hom_feas):         # min_slack, max_lamda, min_cnstr
        if obj < 0.:
            obj_fmt = '%.3e'%obj
        else:
            obj_fmt = ' %.3e'%obj
        if lag < 0.:
            lag_fmt = '%.3e'%lag
        else:
            lag_fmt = ' %.3e'%lag
        if hom < 0.:
            hom_fmt = '%.3e'%hom
        else:
            hom_fmt = ' %.3e'%hom
        self.hist_file.write(
            '%7i' % outer + ' ' * 5 +
            '%5i' % inner + ' ' * 5 +
            '%5i' % self.primal_factory._memory.cost + ' ' * 5 +
            obj_fmt + ' ' * 5 +
            lag_fmt + ' ' * 5 +
            '%.4e' % opt_norm + ' ' * 5 +
            '%.4e' % feas_norm + ' ' * 5 +
            hom_fmt + ' ' * 5 +
            '%.4e' % hom_opt + ' ' * 5 +
            '%.4e' % hom_feas + ' ' * 5 +
            '%1.4f' % self.mu + ' ' * 5 +
            # '%1.4f' % min_slack + ' ' * 5 +
            # '%1.4f' % max_lamda + ' ' * 5 +
            # '%1.4f' % min_cnstr + ' ' * 5 +            
            '\n'
        )

    def _generate_primal(self):
        if self.ineq_factory is None:
            return self.primal_factory.generate()
        else:
            prim = self.primal_factory.generate()
            dual_ineq = self.ineq_factory.generate()        
            return CompositePrimalVector(prim, dual_ineq)

    def _generate_dual(self):

        if self.ineq_factory is not None:
            if self.eq_factory is not None:
                dual_eq = self.eq_factory.generate()
                dual_ineq = self.ineq_factory.generate()
                out = CompositeDualVector(dual_eq, dual_ineq)
            else:    
                out = self.ineq_factory.generate()
        else:
            out = self.eq_factory.generate()

        return  out

    def _generate_kkt(self):
        primal = self._generate_primal()
        dual = self._generate_dual()
        return ReducedKKTVector(primal, dual)

    def _mat_vec(self, in_vec, out_vec):
        self.hessian.product(in_vec, out_vec)
        out_vec.times(1. - self.mu)

        self.prod_work.equals(in_vec)
        self.prod_work.times(self.mu)

        out_vec.primal.plus(self.prod_work.primal)
        out_vec.dual.minus(self.prod_work.dual)

    def _precond(self, in_vec, out_vec):
        if self.mu < 1.:
            self.precond(in_vec, out_vec)
            out_vec.times(1./(1. - self.mu))

        if self.mu > 0.:
            self.prod_work.equals(in_vec)
            self.prod_work.times(1./self.mu)

            out_vec.primal.plus(self.prod_work.primal)
            out_vec.dual.minus(self.prod_work.dual)

    def solve(self):
        self.info_file.write(
            '\n' +
            '**************************************************\n' +
            '***        Using Parameter Continuation        ***\n' +
            '**************************************************\n' +
            '\n')

        # get the vectors we need
        x = self._generate_kkt()
        x0 = self._generate_kkt()
        x_save = self._generate_kkt()
        dJdX = self._generate_kkt()

        dJdX_save = self._generate_kkt()
        dJdX_hom = self._generate_kkt()
        dx = self._generate_kkt()
        dx_newt = self._generate_kkt()
        rhs_vec = self._generate_kkt()
        t = self._generate_kkt()
        t_save = self._generate_kkt()
        self.prod_work = self._generate_kkt()

        state = self.state_factory.generate()
        state_work = self.state_factory.generate()
        state_save = self.state_factory.generate()
        adj = self.state_factory.generate()
        adj_save = self.state_factory.generate()

        primal_work = self._generate_primal()

        dual_work = self._generate_dual()
        dual_work2 = self._generate_dual()

        # dual_workineq = self.ineq_factory.generate()

        if self.ineq_factory is not None:
            self.info_file.write(
                '# of design vars = %i\n' % len(x.primal.design.base.data) +
                '# of slack vars  = %i\n' % len(x.dual.ineq.base.data) +
                '# of eq cnstr    = %i\n' % len(x.dual.eq.base.data) +
                '# of ineq cnstr    = %i\n' % len(x.dual.ineq.base.data) +
                '\n'
            )

        # initialize the problem at the starting point
        x0.equals_init_guess()
        x.equals(x0)

        if not state.equals_primal_solution(x.primal):
            raise RuntimeError('Invalid initial point! State-solve failed.')
        if self.factor_matrices:
            factor_linear_system(x.primal, state)

        # # compute scaling factors
        # adj_save.equals_objective_adjoint(x.primal, state, state_work)

        # if self.ineq_factory is not None:
        #     primal_work.equals_lagrangian_total_gradient(x.primal, state, x.dual, adj_save)
        # else:
        #     primal_work.equals_total_gradient(x.primal, state, adj_save)      

        # obj_norm0 = primal_work.norm2
        # print 'obj_fac', 1./obj_norm0
        # obj_fac = 1./obj_norm0
        # cnstr_fac = 1.
        obj_fac = 1.
        cnstr_fac = 1.

        # compute the lagrangian adjoint
        adj.equals_lagrangian_adjoint(
            x, state, state_work, obj_scale=obj_fac, cnstr_scale=cnstr_fac)
        
        # compute initial KKT conditions
        dJdX.equals_KKT_conditions(
            x, state, adj, obj_scale=obj_fac, cnstr_scale=cnstr_fac)
        # send solution to solver
        solver_info = current_solution(
            num_iter=0, curr_primal=x.primal, curr_state=state, curr_adj=adj,
            curr_dual=x.dual)

        if isinstance(solver_info, str) and solver_info != '':
            self.info_file.write('\n' + solver_info + '\n')

        # compute convergence metrics
        opt_norm00 = dJdX.primal.norm2
        feas_norm00 = dJdX.dual.norm2

        opt_tol = self.primal_tol*opt_norm00
        feas_tol = self.cnstr_tol*feas_norm00
        # self._write_header(opt_tol, feas_tol)

        # write the initial point
        obj00 = objective_value(x.primal, state)

        # ---- Lagrangian needs to be updated to include the slack variable ----
        lag00 = obj_fac * obj00 + cnstr_fac * x0.dual.inner(dJdX.dual)
        
        cost00 = self.primal_factory._memory.cost
        mu00 = self.mu
        # self._write_outer(0, obj0, lag0, opt_norm0, feas_norm0)
        # self.hist_file.write('\n')

        # compute the rhs vector for the predictor problem
        rhs_vec.equals(dJdX)
        rhs_vec.times(-1.)
        
        primal_work.equals(x.primal)
        primal_work.minus(x0.primal)

        rhs_vec.primal.plus(primal_work)

        dual_work.equals(x.dual)
        dual_work.minus(x0.dual)
        rhs_vec.dual.minus(dual_work)

        # compute the tangent vector
        t.equals(0.0)
        self.hessian.linearize(
            x, state, adj,
            obj_scale=obj_fac, cnstr_scale=cnstr_fac)
        # if self.idf_schur is not None:
        #     self.idf_schur.linearize(x, state, scale=cnstr_fac)
        self.krylov.solve(self._mat_vec, rhs_vec, t, self._precond)

        # normalize tangent vector
        tnorm = np.sqrt(t.inner(t) + 1.0)
        t.times(1./tnorm)
        dmu = -1./tnorm

        # START OUTER ITERATIONS
        #########################
        outer_iters = 1
        total_iters = 0

        while self.mu > 0.0 and outer_iters <= self.max_iter:

            self.info_file.write(
                '==================================================\n')
            self.info_file.write(
                'Outer Homotopy iteration %i\n'%(outer_iters))
            self.info_file.write('\n')

            dJdX.equals_KKT_conditions(
                x, state, adj,
                obj_scale=obj_fac, cnstr_scale=cnstr_fac)
            opt_norm = dJdX.primal.norm2
            feas_norm = dJdX.dual.norm2
            self.info_file.write(
                'opt_norm : opt_tol = %e : %e\n'%(
                    opt_norm, opt_tol) +
                'feas_norm : feas_tol = %e : %e\n' % (
                    feas_norm, feas_tol))

            # save current solution in case we need to revert
            x_save.equals(x)
            state_save.equals(state)
            adj_save.equals(adj)
            dJdX_save.equals(dJdX)
            t_save.equals(t)
            dmu_save = dmu
            mu_save = self.mu
            
            # take a predictor step
            # x.equals_ax_p_by(1.0, x, self.step, t)
            dmu_step = dmu * self.step
            dmu_step = max(self.dmu_min, dmu_step)
            dmu_step = min(self.dmu_max, dmu_step)
            self.mu += dmu_step
            if self.mu < 0.0:
                self.mu = 0.0

            self.step = dmu_step/dmu
            x.equals_ax_p_by(1.0, x, self.step, t)

            self.info_file.write('\nmu after pred  = %f\n'%self.mu)

            # print self.mu
            # solve states
            # enforce bounds on the design first
            if self.ineq_factory is not None:
                x.primal.design.enforce_bounds()
            else:
                x.primal.enforce_bounds()

            if not state.equals_primal_solution(x.primal):
                raise RuntimeError(
                    'Invalid predictor point! State-solve failed.')
            if self.factor_matrices:
                factor_linear_system(x.primal, state)

            # # compute adjoint
            adj.equals_lagrangian_adjoint(
                x, state, state_work, obj_scale=obj_fac, cnstr_scale=cnstr_fac)

            # START CORRECTOR (Newton) ITERATIONS
            #####################################
            max_newton = self.inner_maxiter
            if self.mu == 0.0:
                max_newton = 50
                self.krylov.rel_tol=1e-5

            inner_iters = 0
            dx_newt.equals(0.0)
            for i in xrange(max_newton):

                self.info_file.write('\n')
                self.info_file.write('   Inner Newton iteration %i\n'%(i+1))
                self.info_file.write('   -------------------------------\n')

                # compute the KKT conditions
                dJdX.equals_KKT_conditions(
                    x, state, adj,
                    obj_scale=obj_fac, cnstr_scale=cnstr_fac)


                if outer_iters == 1 and inner_iters == 0:
                    # compute convergence metrics
                    opt_norm0 = dJdX.primal.norm2
                    feas_norm0 = dJdX.dual.norm2

                    opt_tol = self.primal_tol*opt_norm0
                    feas_tol = self.cnstr_tol*feas_norm0
                    self._write_header(opt_tol, feas_tol)

                    self._write_outer(0, cost00, obj00, lag00, opt_norm00, feas_norm00, mu00)
                    self.hist_file.write('\n')


                dJdX_hom.equals(dJdX)
                dJdX_hom.times(1. - self.mu)

                # add the primal homotopy term
                primal_work.equals(x.primal)
                primal_work.minus(x0.primal)
                xTx = primal_work.inner(primal_work)

                primal_work.times(self.mu)
                dJdX_hom.primal.plus(primal_work)

                # add the dual homotopy term
                dual_work.equals(x.dual)
                dual_work.minus(x0.dual)
                mTm = dual_work.inner(dual_work)
                dual_work.times(self.mu)
                dJdX_hom.dual.minus(dual_work)
                # get convergence norms
                if inner_iters == 0:
                    # compute optimality norms
                    hom_opt_norm0 = dJdX_hom.primal.norm2
                    hom_opt_norm = hom_opt_norm0
                    hom_opt_tol = self.inner_tol * hom_opt_norm0
                    if hom_opt_tol < opt_tol or self.mu == 0.0:
                        hom_opt_tol = opt_tol
                    # compute feasibility norms
                    hom_feas_norm0 = dJdX_hom.dual.norm2
                    hom_feas_norm = hom_feas_norm0
                    hom_feas_tol = self.inner_tol * hom_feas_norm0
                    if hom_feas_tol < feas_tol or self.mu == 0.0:
                        hom_feas_tol = feas_tol
                else:
                    hom_opt_norm = dJdX_hom.primal.norm2
                    hom_feas_norm = dJdX_hom.dual.norm2
                self.info_file.write(
                    '   hom_opt_norm : hom_opt_tol = %e : %e\n'%(
                        hom_opt_norm, hom_opt_tol) +
                    '   hom_feas_norm : hom_feas_tol = %e : %e\n'%(
                        hom_feas_norm, hom_feas_tol))

                # write inner history
                obj = objective_value(x.primal, state)
                # --------- change here ------------
                lag = obj_fac * obj + cnstr_fac * x.dual.inner(dJdX.dual)

                hom = (1. - self.mu) * lag + 0.5 * self.mu * (xTx - mTm)
                opt_norm = dJdX.primal.norm2
                feas_norm = dJdX.dual.norm2

                # if self.ineq_factory is not None:
                #     min_slack = min(x.primal.slack.base.data) 
                #     max_lamda = max(x.dual.ineq.base.data)
                #     min_cnstr = min(dJdX.dual.ineq.base.data)
                # else:
                #     min_slack = 0.0 
                #     max_lamda = max(x.dual.base.data)
                #     min_cnstr = dJdX.dual.ineq.norm2

                # ------ writing constraints, slack, dual to pickle files for analysis ------
                dual_work2.equals_constraints(x.primal, state)

                if self.ineq_factory is not None:
                    file_name = self.outdir + '/con_%d_%d.pkl'%(outer_iters, inner_iters)
                    output = open(file_name,'w')
                    pickle.dump([x.primal.design.base.data, x.primal.slack.base.data, \
                        x.dual.eq.base.data, x.dual.ineq.base.data, \
                        dual_work2.eq.base.data, dual_work2.ineq.base.data], output)
                    output.close()   


                self._write_inner(
                    outer_iters, inner_iters,
                    obj, lag, opt_norm, feas_norm,
                    hom, hom_opt_norm, hom_feas_norm)
                    # min_slack, max_lamda, min_cnstr)

                # check convergence
                if hom_opt_norm <= hom_opt_tol and hom_feas_norm <= hom_feas_tol:
                    self.info_file.write('\n   Corrector step converged!\n')
                    break

                # linearize the hessian at the new point
                self.hessian.linearize(
                    x, state, adj,
                    obj_scale=obj_fac, cnstr_scale=cnstr_fac)
                if self.idf_schur is not None:
                    self.idf_schur.linearize(x, state, scale=cnstr_fac)

                # define the RHS vector for the homotopy system
                dJdX_hom.times(-1.)

                # solve the system
                dx.equals(0.0)
                self.krylov.solve(self._mat_vec, dJdX_hom, dx, self._precond)
                dx_newt.plus(dx)

                # update the design
                x.plus(dx)
                if self.ineq_factory is not None:
                    x.primal.design.enforce_bounds()
                else:
                    x.primal.enforce_bounds()

                if not state.equals_primal_solution(x.primal):
                    raise RuntimeError('Newton step failed!')
                if self.factor_matrices:
                    factor_linear_system(x.primal, state)

                # compute the adjoint
                adj.equals_lagrangian_adjoint(
                    x, state, state_work,
                    obj_scale=obj_fac, cnstr_scale=cnstr_fac)

                # advance iter counter
                inner_iters += 1
                total_iters += 1

            # if we finished the corrector step at mu=1, we're done!
            if self.mu == 0.0:
                self.info_file.write('\n>> Optimization DONE! <<\n')
                # send solution to solver
                solver_info = current_solution(
                    num_iter=outer_iters, curr_primal=x.primal,
                    curr_state=state, curr_adj=adj, curr_dual=x.dual)
                if isinstance(solver_info, str) and solver_info != '':
                    self.info_file.write('\n' + solver_info + '\n')
                return

            # COMPUTE NEW TANGENT VECTOR
            ############################
            # compute the KKT conditions
            dJdX.equals_KKT_conditions(
                x, state, adj,
                obj_scale=obj_fac, cnstr_scale=cnstr_fac)

            # assemble the predictor RHS
            rhs_vec.equals(dJdX)
            rhs_vec.times(-1.)

            primal_work.equals(x.primal)
            primal_work.minus(x0.primal)

            # if self.ineq_factory is None:
            rhs_vec.primal.plus(primal_work)

            dual_work.equals(x.dual)
            dual_work.minus(x0.dual)
            rhs_vec.dual.minus(dual_work)

            # compute the new tangent vector and predictor step
            t.equals(0.0)
            self.hessian.linearize(
                x, state, adj,
                obj_scale=obj_fac, cnstr_scale=cnstr_fac)
            self.krylov.solve(self._mat_vec, rhs_vec, t, self.precond)

            # normalize the tangent vector
            tnorm = np.sqrt(t.inner(t) + 1.0)
            t.times(1./tnorm)
            dmu = -1./tnorm

            # compute distance to curve
            self.info_file.write('\n')
            dcurve = dx_newt.norm2
            self.info_file.write(
                'dist to curve = %e\n' % dcurve)

            # compute angle between steps
            uTv = t.inner(t_save) + (dmu * dmu_save)
            angl = np.arccos(uTv)
            self.info_file.write(
                'angle         = %f\n' % (angl * 180. / np.pi))

            # compute deceleration factor
            dfac = max(np.sqrt(dcurve / self.nom_dcurve), angl / self.nom_angl)

            self.info_file.write(
                'dfac before cut out  = %f\n' % dfac )

            dfac = max(min(dfac, self.max_factor), self.min_factor)

            # apply the deceleration factor
            self.info_file.write('factor        = %f\n' % dfac)
            self.step /= dfac
            self.info_file.write('step len      = %f\n' % self.step)

            # if factor is bad, go back to the previous point with new factor
            if dfac == self.max_factor:
                self.info_file.write(
                    'High curvature! Rejecting solution...\n')
                # revert solution
                x.equals(x_save)
                self.mu = mu_save
                t.equals(t_save)
                dmu = dmu_save
                state.equals(state_save)
                adj.equals(adj_save)
                dJdX.equals(dJdX_save)
                if self.factor_matrices:
                    factor_linear_system(x.primal, state)
            else:
                # this step is accepted so send it to user
                solver_info = current_solution(
                    num_iter=outer_iters, curr_primal=x.primal,
                    curr_state=state, curr_adj=adj, curr_dual=x.dual)
                if isinstance(solver_info, str) and solver_info != '':
                    self.info_file.write('\n' + solver_info + '\n')

            # advance iteration counter
            outer_iters += 1
            self.info_file.write('\n')
            self.hist_file.write('\n')

# imports here to prevent circular errors
import numpy as np
from kona.options import BadKonaOption, get_opt
from kona.linalg.common import current_solution, factor_linear_system, objective_value
from kona.linalg.vectors.composite import ReducedKKTVector
from kona.linalg.matrices.common import IdentityMatrix
from kona.linalg.matrices.hessian import ReducedKKTMatrix
from kona.linalg.matrices.preconds import ReducedSchurPreconditioner
from kona.linalg.solvers.krylov import FGMRES
from kona.linalg.vectors.composite import CompositePrimalVector
from kona.linalg.vectors.composite import CompositeDualVector