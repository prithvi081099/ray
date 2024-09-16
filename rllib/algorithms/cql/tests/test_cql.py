from pathlib import Path
import os
import unittest

import ray
from ray.rllib.algorithms import cql
from ray.rllib.utils.framework import try_import_torch
from ray.rllib.utils.metrics import (
    ENV_RUNNER_RESULTS,
    EPISODE_RETURN_MEAN,
    EVALUATION_RESULTS,
)
from ray.rllib.utils.test_utils import check_compute_single_action, check_train_results

torch, _ = try_import_torch()


class TestCQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ray.init()

    @classmethod
    def tearDownClass(cls):
        ray.shutdown()

    def test_cql_compilation(self):
        """Test whether CQL can be built with all frameworks."""

        # Learns from a historic-data file.
        # To generate this data, first run:
        # $ ./train.py --run=SAC --env=Pendulum-v1 \
        #   --stop='{"timesteps_total": 50000}' \
        #   --config='{"output": "/tmp/out"}'
        rllib_dir = Path(__file__).parent.parent.parent.parent
        print("rllib dir={}".format(rllib_dir))
        data_file = os.path.join(rllib_dir, "tests/data/pendulum/small.json")
        print("data_file={} exists={}".format(data_file, os.path.isfile(data_file)))

        config = (
            cql.CQLConfig()
            .environment(
                env="Pendulum-v1",
            )
            .offline_data(
                input_=data_file,
                # In the files, we use here for testing, actions have already
                # been normalized.
                # This is usually the case when the file was generated by another
                # RLlib algorithm (e.g. PPO or SAC).
                actions_in_input_normalized=False,
            )
            .training(
                clip_actions=False,
                train_batch_size=2000,
                twin_q=True,
                num_steps_sampled_before_learning_starts=0,
                bc_iters=2,
            )
            .evaluation(
                evaluation_interval=2,
                evaluation_duration=10,
                evaluation_config=cql.CQLConfig.overrides(input_="sampler"),
                evaluation_parallel_to_training=False,
                evaluation_num_env_runners=2,
            )
            .env_runners(num_env_runners=0)
            .reporting(min_time_s_per_iteration=0)
        )
        num_iterations = 4

        algo = config.build()
        for i in range(num_iterations):
            results = algo.train()
            check_train_results(results)
            print(results)
            eval_results = results.get(EVALUATION_RESULTS)
            if eval_results:
                print(
                    f"iter={algo.iteration} "
                    f"R={eval_results[ENV_RUNNER_RESULTS][EPISODE_RETURN_MEAN]}"
                )
        check_compute_single_action(algo)

        # Get policy and model.
        pol = algo.get_policy()
        cql_model = pol.model

        # Example on how to do evaluation on the trained Algorithm
        # using the data from CQL's global replay buffer.
        # Get a sample (MultiAgentBatch).

        batch = algo.env_runner.input_reader.next()
        multi_agent_batch = batch.as_multi_agent()
        # All experiences have been buffered for `default_policy`
        batch = multi_agent_batch.policy_batches["default_policy"]

        obs = torch.from_numpy(batch["obs"])

        # Pass the observations through our model to get the
        # features, which then to pass through the Q-head.
        model_out, _ = cql_model({"obs": obs})
        # The estimated Q-values from the (historic) actions in the batch.
        q_values_old = cql_model.get_q_values(
            model_out, torch.from_numpy(batch["actions"])
        )

        # The estimated Q-values for the new actions computed
        # by our policy.
        actions_new = pol.compute_actions_from_input_dict({"obs": obs})[0]
        q_values_new = cql_model.get_q_values(model_out, torch.from_numpy(actions_new))

        print(f"Q-val batch={q_values_old}")
        print(f"Q-val policy={q_values_new}")

        algo.stop()


if __name__ == "__main__":
    import pytest
    import sys

    sys.exit(pytest.main(["-v", __file__]))
