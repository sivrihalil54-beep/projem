import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

/**
 * Monorepo kök dizini (`web/tests/utils` → üç seviye yukarı).
 */
export function resolveRepositoryRoot(): string {
  return path.resolve(__dirname, '..', '..', '..')
}
