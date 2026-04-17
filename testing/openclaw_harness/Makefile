.PHONY: help harness-up harness-down harness-status harness-logs

CONDA_ENV := massclaw-fed
CONDA_SH := /Users/deepakzedler/miniconda3/etc/profile.d/conda.sh
RUNTIME := /tmp/massclaw_fed
HARNESS_PID := $(RUNTIME)/harness.pid
HARNESS_LOG := $(RUNTIME)/harness.log

help:
	@echo "harness-up       Start the harness server on :19000"
	@echo "harness-down     Stop the harness server"
	@echo "harness-status   Show status"
	@echo "harness-logs     Tail harness server log"

harness-up:
	@mkdir -p $(RUNTIME)
	@if [ -f $(HARNESS_PID) ] && kill -0 $$(cat $(HARNESS_PID)) 2>/dev/null; then \
		echo "harness already running (pid $$(cat $(HARNESS_PID)))"; \
	else \
		bash -c "source $(CONDA_SH) && conda activate $(CONDA_ENV) && cd .. .. && PYTHONPATH=testing/openclaw_harness:backend nohup python -m uvicorn harness.server:app --host 127.0.0.1 --port 19000 --reload > $(HARNESS_LOG) 2>&1 & echo \$$! > $(HARNESS_PID)"; \
		sleep 1; \
		echo "harness started (pid $$(cat $(HARNESS_PID))) — log: $(HARNESS_LOG)"; \
	fi

harness-down:
	@if [ -f $(HARNESS_PID) ]; then \
		PID=$$(cat $(HARNESS_PID)); \
		if kill -0 $$PID 2>/dev/null; then kill $$PID && sleep 1; fi; \
		rm -f $(HARNESS_PID); \
		echo "harness stopped"; \
	else \
		echo "harness not running"; \
	fi

harness-status:
	@if [ -f $(HARNESS_PID) ] && kill -0 $$(cat $(HARNESS_PID)) 2>/dev/null; then \
		echo "harness: RUNNING (pid $$(cat $(HARNESS_PID)))"; \
	else \
		echo "harness: STOPPED"; \
	fi
	@curl -s http://127.0.0.1:19000/health 2>/dev/null || echo "(not reachable)"

harness-logs:
	@tail -n 200 -f $(HARNESS_LOG)
