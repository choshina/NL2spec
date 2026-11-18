.PHONY: validate-runs test-analysis test-experiments

TAG ?= restless_deepstl_test

validate-runs:
	python3 -m restless.evaluation.validate_runs --results results/$(TAG)

test-analysis:
	python3 -m unittest discover -s tests/evaluation -t . -v
	python3 -m tests.pipeline.test_experiment_record

test-experiments:
	$(MAKE) test-analysis
	python3 -m unittest tests.repair.test_sgrl_localization
