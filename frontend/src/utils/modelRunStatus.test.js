import test from "node:test";
import assert from "node:assert/strict";
import { modelRunErrors, modelRunStatus } from "./modelRunStatus.js";

test("historical mixed runs distinguish skipped work from errors", () => {
  const run = { status: "failed", successful_models: 0, failed_models: 1,
    details: { evidence_gated_skipped_jobs: 18, errors: ["SPLG/2y: No rows remain after cleaning"] } };
  assert.match(modelRunStatus(run, "en"), /Skipped; 1/);
  assert.equal(modelRunStatus(run, "en"), "Skipped; 1 ticker error");
  assert.match(modelRunStatus({ ...run, status: "skipped_with_errors" }, "zh"), /已略過；1/);
  assert.equal(modelRunErrors(run)[0], "SPLG/2y: No rows remain after cleaning");
  assert.equal(modelRunStatus({ ...run, details: { ...run.details, errors: ["GLOBAL/2y: provider failed"] } }, "en"), "Skipped; 1 training error");
});

test("fully failed, successful, partial and skipped runs remain distinct", () => {
  assert.equal(modelRunStatus({ status: "failed", failed_models: 18 }, "en"), "Failed");
  assert.equal(modelRunStatus({ status: "success" }, "en"), "Completed");
  assert.equal(modelRunStatus({ status: "partial_success", successful_models: 4 }, "en"), "Partly completed");
  assert.equal(modelRunStatus({ status: "skipped_no_new_evidence" }, "en"), "Skipped: insufficient new evidence");
  assert.match(modelRunStatus({ status: "skipped_no_new_evidence" }), /已略過/);
});

test("errors retain reasons without exposing paths, provider URLs or credentials", () => {
  const errors = modelRunErrors({ details: { errors: [
    "AAPL: No space left on device: 'data/models/AAPL/private/model.pkl'",
    "Fetch failed https://provider.example/?apikey=secret token=private",
    "Failure /home/pi/private/file.db"
  ] } });
  assert.match(errors[0], /No space left/);
  assert.ok(!errors.join(" ").includes("private"));
  assert.ok(!errors.join(" ").includes("provider.example"));
  assert.deepEqual(modelRunErrors({ error_message: "Workflow error" }), ["Workflow error"]);
});
