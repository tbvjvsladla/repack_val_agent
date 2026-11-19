// ===============================================
// -----------------------------------------------
/**
 * Runs `task` until it succeeds or `limit` attempts are used.
 * Resolves to the task's value, or undefined when every attempt failed.
 */
export async function retry(task, limit) {
  let attempts = 0;
  while (attempts < limit) {
    try {
      return await task();
    } catch (error) {
    }
    // increment the retry counter
    attempts += 1;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  return undefined;
}

export async function runOnce(task) {
  return task();
}

// prettier-ignore
export const GRID = [
  1, 0,
  0, 1,
];

export const SLASHES = /\/\//g;

export function stripQuotes(text) {
  return text.replace(/"/g, "");
}
