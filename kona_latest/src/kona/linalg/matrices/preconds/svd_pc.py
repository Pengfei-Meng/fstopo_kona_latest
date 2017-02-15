import numpy as np 
import scipy as sp
from kona.options import get_opt
from kona.linalg.matrices.preconds import LowRankSVD
from kona.linalg.matrices.hessian.basic import BaseHessian
from kona.linalg.solvers.krylov import FGMRES
from kona.linalg.matrices.common import IdentityMatrix
from kona.linalg.vectors.common import DualVectorEQ, DualVectorINEQ
from kona.linalg.vectors.composite import CompositePrimalVector
from kona.linalg.vectors.composite import ReducedKKTVector

class SVDPC(BaseHessian):

    def __init__(self, vector_factories):    

        super(SVDPC, self).__init__(vector_factories, None)
        
        self.primal_factory.request_num_vectors(5)
        self.state_factory.request_num_vectors(2)
        if self.eq_factory is not None:
            self.eq_factory.request_num_vectors(3)
        if self.ineq_factory is not None:
            self.ineq_factory.request_num_vectors(5)

        self.Ag = TotalConstraintJacobian( vector_factories )

        svd_optns = {'lanczos_size': 10}
        self.svd_Ag = LowRankSVD(
            fwd_mat_vec, self.pf, rev_mat_vec, self.df, svd_optns)

        # krylov solver settings
        krylov_optns = {
            'krylov_file'   : 'kona_krylov.dat',
            'subspace_size' : 10,
            'check_res'     : False, 
            'rel_tol'       : 1e-2
        }
        self.krylov = FGMRES(self.primal_factory, krylov_optns,
                             eq_factory=self.eq_factory, ineq_factory=self.ineq_factory)

        self.eye = IdentityMatrix()
        self.eye_precond = self.eye.product
        self._allocated = False

    def fwd_mat_vec(self, in_vec, out_vec):
        self.Ag.product(in_vec, out_vec)

    def rev_mat_vec(self, in_vec, out_vec):
        self.Ag.T.product(in_vec, out_vec)

    def linearize(self, X, state, adjoint, mu=0.0):

        assert isinstance(X.primal, CompositePrimalVector), \
            "SVDPC() linearize >> X.primal must be of CompositePrimalVector type!"
        assert isinstance(X.dual, DualVectorINEQ),  \
            "SVDPC() linearize >> X.dual must be of DualVectorINEQ type!"

        if not self._allocated:
            self.design_work = self.primal_factory.generate()
            self.slack_work = self.ineq_factory.generate()
            self.kkt_work = self._generate_kkt()
            self._allocated = True

        self.at_design = X.primal.design
        self.at_slack = X.primal.slack
        self.at_dual_ineq = X.dual
        self.mu = mu

        self.Ag.linearize(X.primal.design, state)
        self.svd_Ag.linearize()

    def solve(self, rhs_vec, pcd_vec): 
        self.krylov.solve(self._mat_vec, rhs_vec, pcd_vec, self.eye_precond)

    def _mat_vec(self, in_vec, out_vec):
        self._kkt_product(in_vec, out_vec)

        out_vec.times(1. - self.mu)

        self.kkt_work.equals(in_vec)
        self.kkt_work.times(self.mu)

        out_vec.primal.plus(self.kkt_work.primal)
        out_vec.dual.minus(self.kkt_work.dual)

    def _kkt_product(self, in_vec, out_vec):
        # the approximate KKT mat-vec product, with W = Identity, Ag = SVD 
        # expedient coding for now:  only for Graeme's structure problem
        # with only inequality constraints, no equality constraints
        # do some aliasing to make the code cleanier

        assert isinstance(in_vec.primal, CompositePrimalVector), \
            "SVDPC() _kkt_product >> in_vec.primal must be of CompositePrimalVector type!"

        assert isinstance(in_vec.dual, DualVectorINEQ),  \
            "SVDPC() _kkt_product >> in_vec.dual must be of DualVectorINEQ type!"

        in_design = in_vec.primal.design
        in_slack = in_vec.primal.slack
        out_design = out_vec.primal.design
        out_slack = out_vec.primal.slack

        in_dual_ineq = in_vec.dual
        out_dual_ineq = out_vec.dual

        # design block
        out_design.equals(in_design)
        self.Ag.T.product(in_dual_ineq, self.design_work)
        out_design.plus(self.design_work)

        # slack block
        out_slack.equals(in_slack)
        out_slack.times(self.at_dual_ineq)
        self.slack_work.equals(in_dual_ineq)
        self.slack_work.times(self.at_slack)
        out_slack.plus(self.slack_work)
        out_slack.times(-1.0)

        # ineq_dual block
        self.Ag.product(in_design, out_dual_ineq)
        out_dual_ineq.minus(in_slack)

    def _generate_kkt(self):
        prim = self.primal_factory.generate()
        slak = self.ineq_factory.generate()        
        dual = self.ineq_factory.generate()
        ReducedKKTVector(CompositePrimalVector(prim, slak), dual)
