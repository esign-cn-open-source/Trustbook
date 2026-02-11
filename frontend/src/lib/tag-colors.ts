/**
 * Tag color mapping - consistent colors based on tag name hash
 */

const TAG_COLORS = [
  { bg: "bg-red-500/20", text: "text-red-400", border: "border-red-500/30" },
  { bg: "bg-orange-500/20", text: "text-orange-400", border: "border-orange-500/30" },
  { bg: "bg-amber-500/20", text: "text-amber-400", border: "border-amber-500/30" },
  { bg: "bg-yellow-500/20", text: "text-yellow-400", border: "border-yellow-500/30" },
  { bg: "bg-lime-500/20", text: "text-lime-400", border: "border-lime-500/30" },
  { bg: "bg-green-500/20", text: "text-green-400", border: "border-green-500/30" },
  { bg: "bg-emerald-500/20", text: "text-emerald-400", border: "border-emerald-500/30" },
  { bg: "bg-teal-500/20", text: "text-teal-400", border: "border-teal-500/30" },
  { bg: "bg-cyan-500/20", text: "text-cyan-400", border: "border-cyan-500/30" },
  { bg: "bg-sky-500/20", text: "text-sky-400", border: "border-sky-500/30" },
  { bg: "bg-blue-500/20", text: "text-blue-400", border: "border-blue-500/30" },
  { bg: "bg-indigo-500/20", text: "text-indigo-400", border: "border-indigo-500/30" },
  { bg: "bg-violet-500/20", text: "text-violet-400", border: "border-violet-500/30" },
  { bg: "bg-purple-500/20", text: "text-purple-400", border: "border-purple-500/30" },
  { bg: "bg-fuchsia-500/20", text: "text-fuchsia-400", border: "border-fuchsia-500/30" },
  { bg: "bg-pink-500/20", text: "text-pink-400", border: "border-pink-500/30" },
];

function hashString(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash;
  }
  return Math.abs(hash);
}

export function getTagColor(tag: string) {
  const index = hashString(tag.toLowerCase()) % TAG_COLORS.length;
  return TAG_COLORS[index];
}

export function getTagClassName(tag: string): string {
  const color = getTagColor(tag);
  return `${color.bg} ${color.text} ${color.border} border`;
}
