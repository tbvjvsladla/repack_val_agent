#!/usr/bin/env node
import fs from 'node:fs';
import * as experiment from './experiment.mjs';

const [command, root, inputFile, ...extra] = process.argv.slice(2);
const commands = ['init', 'propose', 'request', 'observe', 'evaluate', 'contrast', 'select', 'reflect', 'resume', 'status'];
if (command === '--help' || command === 'help') {
  process.stdout.write('Usage: node src/cli.mjs <' + commands.join('|') + '> <experiment-directory> [input.json]\nAll commands except status require input.json. Paths in JSON resolve from the invocation working directory.\n');
} else {
  try {
    if (!commands.includes(command) || !root || extra.length || (command === 'status' ? Boolean(inputFile) : !inputFile)) {
      throw new Error('invalid arguments; use --help');
    }
    const input = inputFile ? JSON.parse(fs.readFileSync(inputFile, 'utf8')) : undefined;
    process.stdout.write(JSON.stringify(experiment[command](root, input), null, 2) + '\n');
  } catch (error) {
    process.stderr.write(JSON.stringify({ error: error.message }) + '\n');
    process.exitCode = 2;
  }
}
