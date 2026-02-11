"use client";

import {
  useEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ShieldAlert,
  ShieldQuestion,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { SignatureInfo } from "@/lib/api";
import { labelSignatureStatus } from "@/lib/labels";
import { formatDateTimeSeconds } from "@/lib/time-utils";

interface SignatureBadgeProps {
  signature?: SignatureInfo | null;
  className?: string;
  showUnsigned?: boolean;
}

function maskId(raw?: string | null): string | null {
  if (!raw) return null;
  const v = raw.trim();
  if (!v) return null;
  // Common CN ID pattern: keep first 4 + last 6, mask the middle.
  if (/^\d{10,}$/.test(v)) {
    const head = v.slice(0, 4);
    const tail = v.slice(-6);
    const mid = "*".repeat(Math.max(4, v.length - (head.length + tail.length)));
    return `${head}${mid}${tail}`;
  }
  return v;
}

function tryHexToDecimal(serialHex?: string | null): string | null {
  if (!serialHex) return null;
  const v = serialHex.trim();
  if (!v) return null;
  if (!/^[0-9a-fA-F]+$/.test(v)) return v;
  try {
    return BigInt("0x" + v).toString(10);
  } catch {
    return v;
  }
}

export function SignatureBadge({
  signature,
  className,
  showUnsigned = false,
}: SignatureBadgeProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement | null>(null);
  const closeTimerRef = useRef<number | null>(null);
  const status = signature?.status;
  const clearCloseTimer = () => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };
  const scheduleClose = () => {
    clearCloseTimer();
    closeTimerRef.current = window.setTimeout(() => {
      setOpen(false);
    }, 200);
  };
  const handleMouseEnter = () => {
    if (!hasDetail) return;
    clearCloseTimer();
    setOpen(true);
  };
  const handleMouseLeave = (e: ReactMouseEvent<HTMLElement>) => {
    if (!hasDetail) return;
    if (rootRef.current) {
      const nextTarget = e.relatedTarget as Node | null;
      if (nextTarget && rootRef.current.contains(nextTarget)) {
        return;
      }
    }
    scheduleClose();
  };

  useEffect(() => {
    function onDocMouseDown(e: MouseEvent) {
      if (!rootRef.current) return;
      if (rootRef.current.contains(e.target as Node)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, []);
  useEffect(() => {
    return () => {
      clearCloseTimer();
    };
  }, []);

  if (!status) return null;
  if (!showUnsigned && status === "unsigned") return null;

  const hasDetail =
    status !== "unsigned" &&
    Boolean(
      signature?.cert_serial_number_hex ||
      signature?.cert_agent_name ||
      signature?.cert_owner_id ||
      signature?.cert_issuer_cn ||
      signature?.body_sha256 ||
      signature?.ts ||
      signature?.checked_at ||
      signature?.algorithm ||
      signature?.method ||
      signature?.path ||
      signature?.reason ||
      signature?.cert_fingerprint_sha256,
    );

  let badgeNode: ReactNode;
  if (status === "verified") {
    badgeNode = (
      <Badge className={cn("bg-green-600 text-white border-0 h-6 px-3", className)}>
        <CheckCircle2 />
        已验签
      </Badge>
    );
  } else if (status === "invalid") {
    badgeNode = (
      <Badge className={cn("bg-red-600 text-white border-0", className)}>
        <ShieldAlert />
        签名异常
      </Badge>
    );
  } else if (status === "no_cert") {
    badgeNode = (
      <Badge variant="secondary" className={cn(className)}>
        <ShieldQuestion />
        无证书
      </Badge>
    );
  } else if (status === "cert_expired") {
    badgeNode = (
      <Badge
        variant="secondary"
        className={cn("text-yellow-700 dark:text-yellow-400", className)}
      >
        <AlertTriangle />
        证书过期
      </Badge>
    );
  } else {
    badgeNode = (
      <Badge variant="secondary" className={cn(className)}>
        {status}
      </Badge>
    );
  }

  const signerId = maskId(signature?.cert_owner_id);
  const signerName = signature?.cert_agent_name?.trim() || null;
  const signedAt = signature?.ts ? formatDateTimeSeconds(signature.ts) : null;
  const zoneHashBase64 = signature?.body_sha256 || null;
  const serialNo = tryHexToDecimal(signature?.cert_serial_number_hex);
  const issuer = signature?.cert_issuer_cn || null;
  const certValidFrom = signature?.cert_not_before
    ? formatDateTimeSeconds(signature.cert_not_before)
    : null;
  const certValidTo = signature?.cert_not_after
    ? formatDateTimeSeconds(signature.cert_not_after)
    : null;

  return (
    <span
      ref={rootRef}
      className="relative inline-flex items-center"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <span
        role={hasDetail ? "button" : undefined}
        tabIndex={hasDetail ? 0 : -1}
        onClick={(e) => {
          if (!hasDetail) return;
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onKeyDown={(e) => {
          if (!hasDetail) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            e.stopPropagation();
            setOpen((v) => !v);
          }
        }}
        className={hasDetail ? "cursor-pointer" : undefined}
      >
        {badgeNode}
      </span>

      {hasDetail && open && (
        <div
          className="absolute left-0 top-[calc(100%+8px)] z-50 w-[640px] max-w-[92vw] rounded-xl border border-neutral-200 bg-white p-4 text-sm text-neutral-900 shadow-xl dark:border-neutral-800 dark:bg-neutral-950 dark:text-neutral-50"
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          <div className="mb-3 flex items-center justify-between gap-3">
            {status !== "verified" ? (
              <Badge variant="secondary" className="text-xs">
                {labelSignatureStatus(status)}
              </Badge>
            ) : null}
          </div>

          {signature?.reason ? (
            <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300">
              {signature.reason}
            </div>
          ) : null}

          <div className="space-y-4">
            <div>
              <div className="mb-2 font-semibold text-neutral-900 dark:text-neutral-50">
                签名信息
              </div>
              <div className="grid grid-cols-[180px_1fr] gap-x-3 gap-y-2">
                {signerId ? (
                  <>
                    <div className="text-neutral-500 dark:text-neutral-400">
                      责任人ID:
                    </div>
                    <div className="font-medium break-all">{signerId}</div>
                  </>
                ) : null}
                {signerName ? (
                  <>
                    <div className="text-neutral-500 dark:text-neutral-400">
                      Agent名称:
                    </div>
                    <div className="font-medium break-all">{signerName}</div>
                  </>
                ) : null}
                {zoneHashBase64 ? (
                  <>
                    <div className="text-neutral-500 dark:text-neutral-400">
                      保护区Hash(Base64):
                    </div>
                    <div className="font-mono break-all">{zoneHashBase64}</div>
                  </>
                ) : null}
              </div>
            </div>

            <div>
              <div className="mb-2 font-semibold text-neutral-900 dark:text-neutral-50">
                证书信息
              </div>
              <div className="grid grid-cols-[180px_1fr] gap-x-3 gap-y-2">
                {serialNo ? (
                  <>
                    <div className="text-neutral-500 dark:text-neutral-400">
                      序列号:
                    </div>
                    <div className="font-mono break-all">{serialNo}</div>
                  </>
                ) : null}
                {issuer ? (
                  <>
                    <div className="text-neutral-500 dark:text-neutral-400">
                      颁发机构:
                    </div>
                    <div className="font-medium break-all">{issuer}</div>
                  </>
                ) : null}
                {certValidFrom || certValidTo ? (
                  <>
                    <div className="text-neutral-500 dark:text-neutral-400">
                      有效期:
                    </div>
                    <div className="font-mono break-all">
                      {(certValidFrom || "—") +
                        (certValidTo ? ` ~ ${certValidTo}` : "")}
                    </div>
                  </>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      )}
    </span>
  );
}
