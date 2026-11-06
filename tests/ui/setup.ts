import { configure } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MAX_FUNCTIONAL_WATCHDOG_MS } from "./functional-watchdog.js";

configure({ asyncUtilTimeout: MAX_FUNCTIONAL_WATCHDOG_MS });
