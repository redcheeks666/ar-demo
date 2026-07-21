export type RootPublicAssetPath = `/${string}`;

export function withPublicBasePath(
  path: RootPublicAssetPath,
  basePath = import.meta.env.BASE_URL,
): string {
  const normalizedBase = basePath.endsWith('/') ? basePath : `${basePath}/`;
  return `${normalizedBase}${path.slice(1)}`;
}
