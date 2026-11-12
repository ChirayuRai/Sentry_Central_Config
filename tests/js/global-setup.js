import { rmSync } from "node:fs";
import { GENERATED } from "./helpers.js";

// Each test writes snapshot + bridge copies under tests/.generated; start and end clean.
export function setup() {
  rmSync(GENERATED, { recursive: true, force: true });
}

export function teardown() {
  rmSync(GENERATED, { recursive: true, force: true });
}
