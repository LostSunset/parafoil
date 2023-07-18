import dataclasses
import multiprocessing
import pickle
from typing import List, Literal, Optional, Sequence, Tuple, cast
import numpy as np
from pymoo.core.problem import ElementwiseProblem
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.rnd import FloatRandomSampling
from pymoo.optimize import minimize
from pymoo.core.problem import StarmapParallelization
from paraflow.simulation.simulation import run_simulation
from paraflow.passages import SimulationOptions, Passage
from dacite.core import from_dict
from pymoo.core.population import Population
from pymoo.core.evaluator import Evaluator
from ezmesh import visualize_mesh

from parafoil.passages.turbo import TurboStagePassage


class BaseOptimizer(ElementwiseProblem):
    def __init__(
        self,
        working_directory: str,
        passage: Passage,
        sim_options: SimulationOptions,
    ):
        self.working_directory = working_directory
        self.passage = passage
        self.sim_options = sim_options
        self.passage_type = TurboStagePassage
        mins, maxs = get_mins_maxs(passage, self.passage_type)
        self.id = 0

        # num_processes = multiprocessing.cpu_count()
        # pool = multiprocessing.Pool(num_processes)
        # runner = StarmapParallelization(pool.starmap)

        super().__init__(
            n_var=len(mins),
            n_obj=1,
            n_ieq_constr=1,
            xl=mins,
            xu=maxs,
            # elementwise_runner=runner
        )

    def get_passage_candidate(self, x):
        passage = cast(self.passage_type, get_class_from_arr(self.passage, self.passage_type, x))
        self.id += 1
        meshes = passage.get_meshes()
        visualize_mesh(meshes)
        return run_simulation(
            passage,
            sim_options=self.sim_options,
            working_directory=self.working_directory, 
            id=f"{self.id}",
            auto_delete=False,
            # num_procs=8,
            sim_config={
                "custom_executable_path": "/Users/afshawnlotfi/simulators/su2/SU2-7.5.2-macos64",
                # "custom_mpirun_path": "/opt/homebrew/bin/mpirun"
                # "custom_download_url": "https://github.com/OpenOrion/SU2/releases/download/7.5.2/SU2-7.5.2-macos64.zip"
            }
        )

    def _evaluate(self, x, out, *args, **kwargs):
        candidate = self.get_passage_candidate(x)
        
        if "Exit Success (SU2_CFD)" in candidate.log_output:
            efficiency = float(candidate.eval_values.loc[candidate.eval_values.index[-1], '  "TotTotEff[1]"  '])
            # 0 is valid, 0 <= 0 is true
            valid = 0
        else:
            efficiency = 1E10     
            valid = 1     
        out["F"] = [-efficiency]
        out["G"] = [valid]


    def optimize(self, output_file: Optional[str] = None):
        initial_sampling = get_arr_from_class(self.passage, self.passage_type)

        algorithm = NSGA2(
            pop_size=40,
            n_offsprings=10,
            sampling=np.array(initial_sampling),
            crossover=SBX(prob=0.9, eta=15),
            mutation=PM(eta=20),
            eliminate_duplicates=True
        )

        res = minimize(
            self,
            algorithm,
            ("n_gen", 10000),
            seed=1,
            save_history=True,
            verbose=True
        )

        # X, F = res.opt.get("X", "F")
        if output_file:
            with open(output_file, 'wb') as optimization_result_file:
                pickle.dump(res, optimization_result_file, pickle.HIGHEST_PROTOCOL)

        return res


def get_mins_maxs(
    instance,
    cls,
    mins: List[float] = [],
    maxs: List[float] = []
):
    fields = dataclasses.fields(cls)
    for field in fields:
        instance_value = getattr(instance, field.name)
        if field.metadata and "type" in field.metadata:
            if field.metadata["type"] == "range":
                if isinstance(instance_value, Sequence):
                    mins += [field.metadata["min"]] * len(instance_value)
                    maxs += [field.metadata["max"]] * len(instance_value)
                else:
                    mins.append(field.metadata["min"])
                    maxs.append(field.metadata["max"])

            if dataclasses.is_dataclass(field.type) and field.metadata["type"] == "class":
                mins, maxs = get_mins_maxs(instance_value, field.type, mins, maxs)

    return mins, maxs

def get_arr_from_class(
    instance,
    cls,
    instance_values: List[float] = [],
):
    fields = dataclasses.fields(cls)
    for field in fields:
        instance_value = getattr(instance, field.name)
        if field.metadata and "type" in field.metadata:
            if field.metadata["type"] == "range":
                if isinstance(instance_value, Sequence):
                    instance_values += instance_value
                else:
                    instance_values.append(instance_value)

            if dataclasses.is_dataclass(field.type) and field.metadata["type"] == "class":
                instance_values = get_arr_from_class(instance_value, field.type, instance_values)

    return instance_values


def get_class_from_arr(
    instance,
    cls,
    flattened_values: List[float],
    current_index: int = 0,
):
    fields = dataclasses.fields(cls)
    class_dict = {}

    for field in fields:
        attribute_name = field.name
        attribute_metadata = field.metadata
        instance_value = getattr(instance, attribute_name)
        if attribute_metadata:
            if "type" in attribute_metadata:
                if attribute_metadata["type"] == "range":
                    if isinstance(instance_value, Sequence):
                        class_dict[attribute_name] = list(flattened_values[current_index:current_index + len(instance_value)])
                        current_index += len(instance_value)
                    else:
                        class_dict[attribute_name] = flattened_values[current_index]

                        current_index += 1
                if attribute_metadata["type"] == "constant":
                    class_dict[attribute_name] = instance_value

            if dataclasses.is_dataclass(field.type) and attribute_metadata["type"] == "class":
                nested_metadata = get_class_from_arr(instance_value, field.type, flattened_values, current_index)
                class_dict[attribute_name] = nested_metadata
    
    if current_index == 0:
        return from_dict(cls, class_dict)
    return class_dict
