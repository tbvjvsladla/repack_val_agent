// PACKAGE_MANIFEST.json 생성·검증(배포자용) — 패키지 파일별 path·bytes·mode. digest는 적지 않는다.
//   node scripts/package-manifest.ts [패키지 루트]           # 생성(덮어쓰기)
//   node scripts/package-manifest.ts --check [패키지 루트]   # 검증 — 파일 집합·bytes가 다르면 종료코드 1
// manifest는 패키지를 이루는 파일의 목록이다. 내용이 그대로인지는 git(`git status --porcelain`)이나 받은 원본과의
// 직접 비교(`diff -r`)로 확인한다. manifest 자신과 INVENTORY_EXCLUDES는 목록에 넣지 않는다. mode는 git이 보존하는
// 두 값(0644/0755)으로 기록만 하고 비교하지 않는다(실행 비트를 담지 못하는 파일 시스템이 있다). manifest_version 2.0.0.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const MANIFEST = "PACKAGE_MANIFEST.json";
const MANIFEST_VERSION = "2.0.0";
const PACKAGE_NAME = "wiki-desk";
const INVENTORY_EXCLUDES = [MANIFEST, ".git", "node_modules", ".cache"];
const EXEC_BITS = 0o111;
const JSON_INDENT = 2;
const DEFAULT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

type Entry = { path: string; bytes: number; mode: string };
type Manifest = { manifest_version: string; package: string; inventory_excludes: string[]; file_count: number; files: Entry[] };

function inventory(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string): void => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (INVENTORY_EXCLUDES.includes(e.name)) continue;
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.isFile()) out.push(path.relative(root, p).split(path.sep).join("/"));
      else throw new Error(`심볼릭링크·특수 파일은 패키지에 둘 수 없다: ${path.relative(root, p)}`);
    }
  };
  walk(root);
  return out.sort();
}

export function build(root: string): Manifest {
  const files = inventory(root).map((rel): Entry => {
    const abs = path.join(root, rel);
    const stat = fs.statSync(abs);
    const mode = (stat.mode & EXEC_BITS) === 0 ? "0644" : "0755";
    return { path: rel, bytes: stat.size, mode };
  });
  return { manifest_version: MANIFEST_VERSION, package: PACKAGE_NAME, inventory_excludes: INVENTORY_EXCLUDES, file_count: files.length, files };
}

/** 기록된 manifest와 현재 트리의 차이 경로 목록(같으면 빈 배열) */
export function diff(root: string): string[] {
  const have = JSON.parse(fs.readFileSync(path.join(root, MANIFEST), "utf8")) as Manifest;
  const want = build(root);
  const h = new Map(have.files.map((f) => [f.path, f.bytes]));
  const w = new Map(want.files.map((f) => [f.path, f.bytes]));
  return [...new Set([...h.keys(), ...w.keys()])].filter((p) => h.get(p) !== w.get(p)).sort();
}

if (process.argv[1] !== undefined && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const check = args.includes("--check");
  const root = args.find((a) => a !== "--check") ?? DEFAULT_ROOT;
  if (check) {
    const d = diff(root);
    console.log(d.length === 0 ? `MANIFEST OK (${build(root).file_count} files)` : `MANIFEST MISMATCH: ${d.join(", ")}`);
    process.exit(d.length === 0 ? 0 : 1);
  }
  const m = build(root);
  fs.writeFileSync(path.join(root, MANIFEST), JSON.stringify(m, null, JSON_INDENT) + "\n");
  console.log(`MANIFEST written (${m.file_count} files)`);
}
