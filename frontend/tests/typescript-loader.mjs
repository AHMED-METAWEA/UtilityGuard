import ts from 'typescript';
import { readFile, stat } from 'node:fs/promises';

// Keep the tests on Node's built-in runner without adding another test framework.
export async function resolve(specifier, context, next) {
  try { return await next(specifier, context); }
  catch (error) {
    if (!specifier.startsWith('.')) throw error;
    for (const suffix of ['.ts', '.tsx', '/index.ts']) {
      const candidate = new URL(specifier + suffix, context.parentURL);
      try { if ((await stat(candidate)).isFile()) return { url: candidate.href, shortCircuit: true }; }
      catch { /* Try the next TypeScript source path. */ }
    }
    throw error;
  }
}
export async function load(url, context, next) {
  if (!/\.tsx?$/.test(url)) return next(url, context);
  const source = await readFile(new URL(url), 'utf8');
  return { format: 'module', shortCircuit: true, source: ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext } }).outputText };
}
