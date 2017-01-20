import numpy as np 
import scipy as sp
from kona.options import get_opt
from kona.linalg.matrices.hessian.basic import BaseHessian
import pdb
from kona.linalg.vectors.composite import ReducedKKTVector
from kona.linalg.vectors.composite import CompositePrimalVector
from kona.linalg.vectors.composite import CompositeDualVector

class APPROXADJOINT(BaseHessian):
    """
    Specially for the ASO problem, with eq, ineq constraints both present
    This object is a preconditioner for the KKT system using approximate adjoints. 
    Explicit matrices, together with approximate adjointed constrained Jacobian, 
    are explicitly factorized to carry out the precondition work. 
    The precondition system is :

    .. math::
        \\begin{bmatrix}
        \\mathcal{W} && 0 && \tilda{A_h}^T && \tilda{A_g}^T \\\\
        \\ 0   &&   -\Gamma_g  &&  0  &&  -S \\\\
        \\ \tilda{A_h}  &&   0  &&  0  &&  0 \\\\
        \\ \tilda{A_g}  &&   -I  &&  0  &&  0 
        \\end{bmatrix}
        \\begin{bmatrix}
        \\ v_x \\\\
        \\ v_s \\\\
        \\ v_h \\\\
        \\ v_g 
        \\end{bmatrix}
        =
        \\begin{bmatrix}
        \\ u_x \\\\
        \\ u_s \\\\
        \\ u_h \\\\
        \\ u_g 
        \\end{bmatrix}
    """

    def __init__(self, vector_factories):    

        super(APPROXADJOINT, self).__init__(vector_factories, None)
        
        self.primal_factory.request_num_vectors(5)
        self.state_factory.request_num_vectors(2)
        if self.eq_factory is not None:
            self.eq_factory.request_num_vectors(3)
        if self.ineq_factory is not None:
            self.ineq_factory.request_num_vectors(5)

        # self.W = IdentityMatrix()
        self.W = LagrangianHessian( vector_factories )
        self.Ag = TotalConstraintJacobian( vector_factories )

        self._allocated = False

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

    def linearize(self, X, state, adjoint, mu=0.0):

        self.W.linearize(X, state, adjoint)
        self.Ag.linearize(X.primal.design, state)

        self.at_design = X.primal.design.base.data
        self.at_slack = X.primal.slack.base.data
        if self.eq_factory is not None:
            self.at_dual_eq = X.dual.eq.base.data
            self.at_dual_ineq = X.dual.ineq.base.data
        else:
            self.at_dual_ineq = X.dual.base.data

        self.at_slack_kona = X.primal.slack

        self.mu = mu

        # make self.at_slack all positive and minimum value 0.1
        # self.at_slack[self.at_slack < 0.1] = 0.1

    
    def solve(self, in_vec, out_vec):  
        # in_vec  : to be preconditioned
        # out_vec : after preconditioned
        # note: you cannot change in_vec!!!!!!! 

        # out_vec.equals(in_vec)

        # specifically for Graeme's Problem
        v_x = in_vec.primal.design.base.data
        v_s = in_vec.primal.slack.base.data
        v_g = in_vec.dual.base.data

        rhs_full = np.hstack([v_x, v_s, v_g])

        # multiplying Ag_nonlinear by i-th eye vector, with the i-th entry 1, others 0
        # to retrieve the approx. Ag_nonlinear entries. only Cl, Cmy nonlinear constraints here

        in_design = self.primal_factory.generate()
        out_design = self.primal_factory.generate()
        out_dual = self.ineq_factory.generate()

        num_design = len(self.at_design)
        num_slack = len(self.at_slack)
        num_ineq = len(self.at_dual_ineq)

        W_full = np.zeros((num_design, num_design))
        A_full = np.zeros((num_ineq, num_design))

        # loop over design variables and start assembling the matrices
        for i in xrange(num_design):
            # print 'Evaluating design var:', i+1
            # set the input vector so that we only pluck out one column of the matrix
            in_design.equals(0.0)
            in_design.base.data[i] = 1.
            # perform the Lagrangian Hessian product and store
            self.W.approx.multiply_W(in_design, out_design)
            W_full[:, i] = out_design.base.data
            # perform the Constraint Jacobian product and store
            self.Ag.approx.product(in_design, out_dual)
            A_full[:, i] = out_dual.base.data

        # # Ag_nonlinear
        # # Ag_linear
        # # ----------------- The Full KKT Matrix -------------------

        # KKT_full = np.vstack([np.hstack([np.eye(num_design), np.zeros((num_design, num_ineq)),  Ah.transpose(),  Ag_T]), 
        #                       np.hstack([np.zeros((num_ineq, num_design)),  -np.diag(self.at_dual_ineq),    np.zeros((num_ineq, num_eq)),  -np.diag(self.at_slack)]),
        #                       np.hstack([Ah, np.zeros((num_eq, num_ineq + num_eq + num_ineq))]),
        #                       np.hstack([Ag, -np.eye(num_ineq),  np.zeros((num_ineq, num_eq + num_ineq))]) ])

        # eyes_h = np.hstack([ np.ones(num_design), np.ones(num_ineq), -np.ones(num_eq), -np.ones(num_ineq) ])   #self.at_slack, 
        # homo_I = np.diag(eyes_h)

        # np.eye(num_design)
        KKT_full = np.vstack([np.hstack([W_full,  np.zeros((num_design, num_ineq)),  A_full.transpose()]), 
                              np.hstack([np.zeros((num_ineq, num_design)),  -np.diag(self.at_dual_ineq), -np.diag(self.at_slack)]),
                              np.hstack([A_full, -np.eye(num_ineq),  np.zeros((num_ineq, num_ineq))]) ])

        eyes_h = np.hstack([ np.ones(num_design), np.ones(num_ineq), -np.ones(num_ineq) ])   #self.at_slack, 
        homo_I = np.diag(eyes_h)        

        #------------------------------------------------------------------         
        KKT = (1-self.mu)*KKT_full + self.mu*homo_I

        p_full = sp.linalg.lu_solve(sp.linalg.lu_factor(KKT), rhs_full)

        p_x = p_full[:num_design]
        p_s = p_full[num_design:num_design + num_ineq]   
        p_g = p_full[num_design + num_ineq:]

        out_vec.primal.design.base.data = p_x
        out_vec.primal.slack.base.data = p_s
        out_vec.dual.base.data = p_g

        # in_vec_work.equals(out_vec)
        # in_vec_work.minus(in_vec)
        # diff_vec_norm = in_vec_work.norm2

        # # out_vec.primal.slack.times(self.at_slack_kona) 
        # # ------------------------------------
        # if in_vec._memory.solver.get_rank() == 0:
        #     # print 'np.linalg.cond(KKT_full) : ',  np.linalg.cond(KKT_full)
        #     print '......... PC approx_adjoint called .........'
        #     print 'Residual : ', np.linalg.norm(np.dot(KKT, p_full) - rhs_full) 
        #     print 'in_vec.norm2', in_vec.norm2
        #     print 'out_vec.norm2', out_vec.norm2
        #     print 'out_in_diff.norm2', diff_vec_norm


        # ---------- including the homotopy mu part --------------
        

from kona.linalg.matrices.hessian import TotalConstraintJacobian
from kona.linalg.matrices.common import IdentityMatrix
from kona.linalg.matrices.hessian import LagrangianHessian
from kona.linalg.vectors.common import DesignVector, StateVector
from kona.linalg.vectors.common import DualVectorEQ, DualVectorINEQ
from kona.linalg.vectors.composite import CompositeDualVector
from kona.linalg.matrices.common import dRdX, dRdU, dCdX, dCdU
from kona.linalg.matrices.common import dCdX_total_linear
