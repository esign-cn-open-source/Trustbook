import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { SignatureInfo } from "@/lib/api";

interface AgentVerifiedBadgeProps {
  signature?: SignatureInfo | null;
  className?: string;
}

export function AgentVerifiedBadge({ signature, className }: AgentVerifiedBadgeProps) {
  if (signature?.status !== "verified") return null;
  return (
    <Badge className={cn("bg-emerald-600 text-white border-0 h-6 px-3", className)}>
      已验证
    </Badge>
  );
}
