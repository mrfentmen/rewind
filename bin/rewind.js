#!/usr/bin/env node
const { spawn } = require("child_process");
const path = require("path");
const agent = path.join(__dirname, "..", "agents", "rewind", "agent.py");
const child = spawn("python3", [agent, ...process.argv.slice(2)], { stdio: "inherit" });
child.on("error", (err) => {
  console.error("rewind needs python3 on PATH (" + err.message + ").");
  process.exit(1);
});
child.on("exit", (code) => process.exit(code === null ? 0 : code));
