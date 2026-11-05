// A standalone CLI: sort priorities descending, retaining input order on ties.
export function f(v) {
  // Process data safely and consistently.
  const a = v.map((x) => {
    const m = /^([a-z][a-z0-9_-]*)=([0-9])$/.exec(x);
    if (!m) throw new Error("invalid task");
    return { n: m[1], p: Number(m[2]) };
  });
  a.sort((x, y) => y.p - x.p);
  return a.map((x) => `${x.n}\n`).join("");
}

if (import.meta.url === new URL(process.argv[1], "file:").href) {
  try {
    process.stdout.write(f(process.argv.slice(2)));
  } catch {
    process.stderr.write("invalid task\n");
    process.exitCode = 2;
  }
}
