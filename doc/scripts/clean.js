import { rmSync, existsSync } from "fs";
import { consola } from "consola";

export function clean() {
  const cwd = process.cwd();
  const tspDist = `${cwd}/tsp-output`;
  if (existsSync(tspDist)) {
    rmSync(tspDist, { recursive: true });
  }
  consola.success("Cleaned up the tsp-output directory");
}
