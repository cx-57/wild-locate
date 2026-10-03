"""JSON-lines subprocess entry points used by the desktop GUI."""

import argparse
from contextlib import redirect_stdout
import json
import sys
import traceback


def run_prediction(account=None):
    with redirect_stdout(sys.stderr):
        from wildlocate.core.predict import PredictionError, assess_habitat

    for line in sys.stdin:
        try:
            request = json.loads(line)
            with redirect_stdout(sys.stderr):
                result = assess_habitat(
                    request["species"],
                    request["latitude"],
                    request["longitude"],
                    request.get("region", "MA"),
                    username=account,
                    radius_km=request.get("radius_km"),
                )
            response = {"result": result}
        except PredictionError as exc:
            response = {"error": str(exc), "code": exc.code}
        except Exception:
            traceback.print_exc(file=sys.stderr)
            response = {
                "error": "The analysis request could not be completed. Please try again.",
                "code": "worker_error",
            }
        print(json.dumps(response, allow_nan=False), flush=True)


def run_deep_dive(account=None):
    with redirect_stdout(sys.stderr):
        from wildlocate.core.deep_dive import analyze_deep_dive

    for line in sys.stdin:
        try:
            request = json.loads(line)
            with redirect_stdout(sys.stderr):
                result = analyze_deep_dive(
                    request["species"],
                    request["latitude"],
                    request["longitude"],
                    request["radius_km"],
                    request.get("region", "MA"),
                    username=account,
                )
            response = {"result": result}
        except Exception:
            traceback.print_exc(file=sys.stderr)
            response = {
                "error": "The Deep Dive could not be completed. Check the selected model and environmental data, then try again.",
                "code": "deep_dive_error",
            }
        print(json.dumps(response, allow_nan=False), flush=True)


def run_training(job_id, region, account):
    from requests.exceptions import RequestException
    from wildlocate.core.registry import cleanup_job
    from wildlocate.core.modeling import TrainingSession

    output = sys.stdout

    def emit(event, **payload):
        output.write(json.dumps({"event": event, **payload}, allow_nan=False) + "\n")
        output.flush()

    session = TrainingSession(
        job_id,
        lambda message: emit("progress", message=message),
        region=region,
        username=account,
    )
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                with redirect_stdout(sys.stderr):
                    action = request.get("action")
                    if action == "resolve":
                        result, event = session.resolve(request["query"]), "resolved"
                    elif action == "prepare":
                        result, event = session.prepare(), "prepared"
                    elif action == "train":
                        result, event = session.train(), "completed"
                    elif action == "initialize":
                        result, event = session.initialize_environment(), "initialized"
                    else:
                        raise ValueError("Unknown training action.")
                emit(event, **result)
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                missing = isinstance(exc, FileNotFoundError) and "Missing required raw environmental datasets" in str(exc)
                if missing:
                    message = "Environmental datasets are missing. Download them below, then check the species data again."
                elif isinstance(exc, RequestException):
                    message = "Data could not be downloaded. Check your internet connection and try again. See Training details for the server response."
                else:
                    message = str(exc)
                emit("error", message=message, code="missing_environment" if missing else "training_failed")
    finally:
        cleanup_job(job_id, username=session.username)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)

    prediction = subparsers.add_parser("predict")
    prediction.add_argument("--account")

    deep_dive = subparsers.add_parser("deep-dive")
    deep_dive.add_argument("--account")

    training = subparsers.add_parser("train")
    training.add_argument("--job-id", required=True)
    training.add_argument("--region", default="MA")
    training.add_argument("--account", required=True)

    args = parser.parse_args()
    if args.mode == "predict":
        run_prediction(args.account)
    elif args.mode == "deep-dive":
        run_deep_dive(args.account)
    else:
        run_training(args.job_id, args.region, args.account)


if __name__ == "__main__":
    main()
