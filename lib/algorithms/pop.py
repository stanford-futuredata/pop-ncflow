from ..lp_solver import LpSolver
from ..partitioning.pop import (
    SmartSplitter,
    BaselineSplitter,
    GenericSplitter,
    RandomSplitter,
)
from ..runtime_utils import parallelized_rt
from ..graph_utils import path_to_edge_list
from ..path_utils import find_paths, graph_copy_with_edge_weights, remove_cycles
from ..config import TOPOLOGIES_DIR
from .abstract_formulation import Objective
from .path_formulation import PathFormulation
from gurobipy import GRB, Model, quicksum
from collections import defaultdict
from pathos import multiprocessing
import numpy as np
import math
import random
import re
import os
import time
import pickle

PATHS_DIR = os.path.join(TOPOLOGIES_DIR, "paths", "path-form")


class POP(PathFormulation):
    @classmethod
    def new_total_flow(
        cls,
        num_subproblems,
        split_method,
        split_fraction,
        num_paths=4,
        edge_disjoint=True,
        dist_metric="inv-cap",
        out=None,
    ):
        return cls(
            objective=Objective.TOTAL_FLOW,
            num_subproblems=num_subproblems,
            split_method=split_method,
            split_fraction=split_fraction,
            num_paths=num_paths,
            edge_disjoint=edge_disjoint,
            dist_metric=dist_metric,
            DEBUG=True,
            VERBOSE=False,
            out=out,
        )

    @classmethod
    def new_min_max_link_util(
        cls,
        num_subproblems,
        split_method,
        split_fraction,
        num_paths=4,
        edge_disjoint=True,
        dist_metric="inv-cap",
        out=None,
    ):
        return cls(
            objective=Objective.MIN_MAX_LINK_UTIL,
            num_subproblems=num_subproblems,
            split_method=split_method,
            split_fraction=split_fraction,
            num_paths=num_paths,
            edge_disjoint=edge_disjoint,
            dist_metric=dist_metric,
            DEBUG=True,
            VERBOSE=False,
            out=out,
        )

    @classmethod
    def new_max_concurrent_flow(
        cls,
        num_subproblems,
        split_method,
        split_fraction,
        num_paths=4,
        edge_disjoint=True,
        dist_metric="inv-cap",
        out=None,
    ):
        return cls(
            objective=Objective.MAX_CONCURRENT_FLOW,
            num_subproblems=num_subproblems,
            split_method=split_method,
            split_fraction=split_fraction,
            num_paths=num_paths,
            edge_disjoint=edge_disjoint,
            dist_metric=dist_metric,
            DEBUG=True,
            VERBOSE=False,
            out=out,
        )

    def __init__(
        self,
        *,
        objective,
        num_subproblems,
        split_method,
        split_fraction,
        num_paths=4,
        edge_disjoint=True,
        dist_metric="inv-cap",
        DEBUG=True,
        VERBOSE=False,
        out=None,
    ):
        super().__init__(
            objective=objective,
            num_paths=num_paths,
            edge_disjoint=edge_disjoint,
            dist_metric=dist_metric,
            DEBUG=DEBUG,
            VERBOSE=VERBOSE,
            out=out,
        )
        self._num_subproblems = num_subproblems
        self._split_method = split_method
        self._split_fraction = split_fraction

    def split_problems(self, problem):
        splitter = None
        if self._split_method == "skewed":
            splitter = BaselineSplitter(self._num_subproblems)
        elif self._split_method == "random":
            splitter = RandomSplitter(self._num_subproblems, self._split_fraction)
        elif self._split_method in ["tailored", "means", "covs"]:
            pf_original = PathFormulation.get_pf_for_obj(
                self._objective, self._num_paths
            )
            if self._split_method == "tailored":
                paths_dict = pf_original.compute_paths(problem)
                splitter = SmartSplitter(self._num_subproblems, paths_dict)
            else:
                splitter = GenericSplitter(
                    self._num_subproblems, pf_original, self._split_method, self._split_fraction
                )
        else:
            raise Exception("Invalid split_method {}".format(self._split_method))

        return splitter.split(problem)

    ###############################
    # Override superclass methods #
    ###############################

    def solve_subproblem(self, index):
        pf = self._pfs[index]
        subproblem = self._subproblem_list[index]
        pf.solve(subproblem, num_threads=1)
        return (pf.runtime, pf.sol_dict)

    def solve(self, problem):
        self._problem = problem
        self._subproblem_list = self.split_problems(problem)
        self._pfs = [
            PathFormulation.get_pf_for_obj(self._objective, self._num_paths)
            for subproblem in self._subproblem_list
        ]
        pool = multiprocessing.ProcessPool(self._num_subproblems)
        results = pool.map(self.solve_subproblem, range(self._num_subproblems))
        for (runtime, sol_dict), pf in zip(results, self._pfs):
            pf._runtime = runtime
            pf._sol_dict = sol_dict

    @property
    def sol_dict(self):
        if not hasattr(self, "_sol_dict"):
            sol_dicts = [pf.sol_dict for pf in self._pfs]
            merged_sol_dict = defaultdict(list)
            for sol_dict in sol_dicts:
                for (_, (src, target, _)), flow_list in sol_dict.items():
                    merged_sol_dict[(src, target)] += flow_list
            self._sol_dict = {
                commod_key: merged_sol_dict[(commod_key[-1][0], commod_key[-1][1])]
                for commod_key in self.problem.commodity_list
            }

        return self._sol_dict

    @property
    def sol_mat(self):
        raise NotImplementedError(
            "sol_mat needs to be implemented in the subclass: {}".format(self.__class__)
        )

    def runtime_est(self, num_threads):
        return parallelized_rt(
            [pf.runtime for pf in self._pfs], num_threads
        )

    @property
    def runtime(self):
        return sum([pf.runtime for pf in self._pfs])
