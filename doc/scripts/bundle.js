import { existsSync, rmSync, mkdirSync, cpSync } from "fs";
import { join } from "path";
import { consola } from "consola";

export const bundle = () => {
  const cwd = process.cwd();
  const dist = `${cwd}/dist`;
  const typeSpecDir = join(cwd, "tsp-output", "@typespec");
  const publicDir = join(cwd, "public");
  if (existsSync(dist)) {
    rmSync(dist, { recursive: true });
    consola.success("Cleaned up the dist directory");
  }
  //
  mkdirSync(dist);
  if (existsSync(typeSpecDir)) {
    cpSync(typeSpecDir, `${dist}`, { recursive: true });
  } else {
    consola.error("No typespec directory found");
  }
  if (existsSync(publicDir)) {
    cpSync(publicDir, `${dist}`, { recursive: true });
  } else {
    consola.error("No public directory found");
  }
  //
  consola.success("Bundled the typespec and public directories into the dist");
};
