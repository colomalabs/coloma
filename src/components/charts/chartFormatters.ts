export function formatSeconds(value: number) {
  return value >= 10 ? value.toFixed(1) : value.toFixed(2);
}

export function formatTokens(value: number) {
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : value.toFixed(0);
}
