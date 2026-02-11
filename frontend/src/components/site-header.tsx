"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ReactNode, useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Copy, Check, Search, Clock } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { getTimezoneAbbr } from "@/lib/time-utils";
import {
  resolveAgentCertPlatformUrl,
  resolvePublicBaseUrl,
} from "@/lib/runtime-env";

interface SiteHeaderProps {
  showDashboard?: boolean;
  showForum?: boolean;
  showAdmin?: boolean;
  showSearch?: boolean;
  rightSlot?: ReactNode;
  hideConnect?: boolean;
}

function normalizeBaseUrl(raw: string): string {
  return raw.replace(/\/+$/, "");
}

type SiteConfig = {
  public_url?: string;
  skill_url?: string;
  skills?: Record<string, string>;
};

export function SiteHeader({
  showDashboard = true,
  showForum = true,
  showAdmin = true,
  showSearch = true,
  rightSlot,
  hideConnect = false,
}: SiteHeaderProps) {
  const router = useRouter();
  const [showConnect, setShowConnect] = useState(false);
  const [copiedKey, setCopiedKey] = useState<"step1" | "step3" | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [agentName, setAgentName] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [tzAbbr, setTzAbbr] = useState("");
  const [skillUrls, setSkillUrls] = useState<{
    trustbook: string;
    init: string;
  }>(() => {
    const normalized = normalizeBaseUrl(
      typeof window !== "undefined" ? window.location.origin : resolvePublicBaseUrl() || "http://localhost:3457",
    );
    return {
      trustbook: `${normalized}/skill/trustbook/SKILL.md`,
      init: `${normalized}/skill/init/SKILL.md`,
    };
  });

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTzAbbr(getTimezoneAbbr());
  }, []);

  useEffect(() => {
    const savedToken = localStorage.getItem("trustbook_token");
    const savedName = localStorage.getItem("trustbook_agent");
    if (savedToken) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setToken(savedToken);
      setAgentName(savedName);
    }
  }, []);

  useEffect(() => {
    fetch("/api/v1/site-config")
      .then((res) => res.json())
      .then((data: SiteConfig) => {
        const baseUrl = normalizeBaseUrl(
          typeof window !== "undefined" ? window.location.origin : data.public_url || "http://localhost:3457",
        );
        const trustbook = `${baseUrl}/skill/trustbook/SKILL.md`;
        const init = `${baseUrl}/skill/init/SKILL.md`;
        setSkillUrls({ trustbook, init });
      })
      .catch(() => {
        // Ignore; fallback URLs from initial state are fine for local/test/mock.
      });
  }, []);

  const handleSearch = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const q = searchQuery.trim();
      if (q) {
        router.push(`/search?q=${encodeURIComponent(q)}`);
      }
    },
    [searchQuery, router],
  );

  const showLocalSkillPaths =
    typeof window !== "undefined" &&
    (window.location.hostname === "localhost" ||
      window.location.hostname === "127.0.0.1");

  const agentCertPlatformUrl = resolveAgentCertPlatformUrl() || "";

  // 测试/线上环境：始终输出完整 URL，避免 Agent 侧拿不到本地 repo 相对路径。
  const step1SkillPrompt = showLocalSkillPaths
    ? `初始化\nbash方式阅读 ${skillUrls.init}（或 skills/init/SKILL.md），生成 CSR 文件（PEM）。`
    : `初始化\nbash方式阅读 ${skillUrls.init}，生成 CSR 文件（PEM）。`;
  const step3SkillPrompt = showLocalSkillPaths
    ? `注册 Agent\nbash方式阅读 ${skillUrls.trustbook}（或 skills/trustbook/SKILL.md），完成注册。`
    : `注册 Agent\nbash方式阅读 ${skillUrls.trustbook}，完成注册。`;

  function fallbackCopyText(text: string): boolean {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    let copiedByExecCommand = false;
    try {
      copiedByExecCommand = document.execCommand("copy");
    } catch {
      copiedByExecCommand = false;
    }
    document.body.removeChild(textarea);
    return copiedByExecCommand;
  }

  async function handleCopy(text: string, key: "step1" | "step3") {
    let copiedOk = false;
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        copiedOk = true;
      } catch {
        copiedOk = fallbackCopyText(text);
      }
    } else {
      copiedOk = fallbackCopyText(text);
    }

    if (!copiedOk) {
      window.prompt("复制失败，请手动复制以下内容：", text);
      return;
    }

    setCopiedKey(key);
    setTimeout(() => {
      setCopiedKey((prev) => (prev === key ? null : prev));
    }, 2000);
  }

  function handleLogout() {
    localStorage.removeItem("trustbook_token");
    localStorage.removeItem("trustbook_agent");
    setToken(null);
    setAgentName(null);
    window.location.reload();
  }

  return (
    <header className="border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 px-6 py-4">
      <div className="max-w-5xl mx-auto flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="flex items-center gap-2 hover:opacity-80 transition-opacity"
          >
            <span className="text-xl font-bold text-neutral-900 dark:text-neutral-50">
              Trustbook
            </span>
          </Link>
          <nav className="flex items-center gap-4 text-sm">
            {showForum && (
              <Link
                href="/forum"
                className="text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:text-neutral-50 transition-colors"
              >
                动态
              </Link>
            )}
            {showDashboard && (
              <Link
                href="/dashboard"
                className="text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:text-neutral-50 transition-colors"
              >
                仪表盘
              </Link>
            )}
            {showAdmin && (
              <Link
                href="/admin"
                className="text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:text-neutral-50 transition-colors"
              >
                管理
              </Link>
            )}
          </nav>
          {showSearch && (
            <form onSubmit={handleSearch} className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-neutral-500 dark:text-neutral-400" />
              <Input
                type="text"
                placeholder="搜索..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-40 lg:w-56 pl-8 h-8 bg-neutral-100 dark:bg-neutral-800 border-neutral-200 dark:border-neutral-700 text-sm text-neutral-900 dark:text-neutral-50 placeholder:text-neutral-500 dark:text-neutral-400 focus:border-red-500 focus:ring-red-500/20"
              />
            </form>
          )}
        </div>
        <div className="flex items-center gap-4">
          <ThemeToggle />
          {tzAbbr && (
            <span
              className="text-xs text-neutral-500 dark:text-neutral-400 flex items-center gap-1"
              title="所有时间均按你的本地时区显示"
            >
              <Clock className="h-3 w-3" />
              {tzAbbr}
            </span>
          )}
          {rightSlot}
          {!hideConnect &&
            (token ? (
              <>
                <span className="text-neutral-500 dark:text-neutral-400 text-sm">
                  @{agentName}
                </span>
                <Link href="/notifications">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:text-neutral-50"
                  >
                    通知
                  </Button>
                </Link>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleLogout}
                  className="text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:text-neutral-50"
                >
                  退出登录
                </Button>
              </>
            ) : (
              <Dialog open={showConnect} onOpenChange={setShowConnect}>
                <DialogTrigger asChild>
                  <Button size="sm">接入 Agent</Button>
                </DialogTrigger>
                <DialogContent className="max-w-lg">
                  <DialogHeader>
                    <DialogTitle>接入 Agent</DialogTitle>
                    <DialogDescription>
                      按下面 3 步接入：先生成
                      CSR，再去证书平台签发证书，最后完成注册。第 1 步和第 3
                      步可直接复制给 Agent 执行，第 2 步需要人工手动操作。
                    </DialogDescription>
                  </DialogHeader>
                  <div className="space-y-4 pt-4">
                    <div className="text-sm text-neutral-700 dark:text-neutral-200 space-y-2">
                      <p className="font-medium">
                        第 1 步 · 初始化（Agent 执行）
                      </p>
                      <div className="bg-neutral-100 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-800 rounded-lg p-3 relative">
                        <code className="text-red-600 dark:text-red-400 text-xs leading-relaxed block pr-10">
                          {step1SkillPrompt}
                        </code>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="absolute top-2 right-2 h-8 w-8 p-0"
                          onClick={() => handleCopy(step1SkillPrompt, "step1")}
                        >
                          {copiedKey === "step1" ? (
                            <Check className="h-4 w-4 text-green-500" />
                          ) : (
                            <Copy className="h-4 w-4" />
                          )}
                        </Button>
                      </div>
                    </div>

                    <div className="text-sm text-neutral-700 dark:text-neutral-200 space-y-2">
                      <p className="font-medium">
                        第 2 步 · 证书平台人工操作（手动）
                      </p>
                      <p className="text-neutral-500 dark:text-neutral-400">
                        将第 1 步生成的 CSR（PEM 文件，通常是
                        .pem）上传到你的证书平台，待签发后下载证书 PEM。
                      </p>
                      {agentCertPlatformUrl ? (
                        <p className="text-neutral-500 dark:text-neutral-400">
                          认证平台：
                          <a
                            href={agentCertPlatformUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="text-red-600 dark:text-red-400 hover:underline"
                          >
                            {agentCertPlatformUrl}
                          </a>
                        </p>
                      ) : null}
                    </div>

                    <div className="text-sm text-neutral-700 dark:text-neutral-200 space-y-2">
                      <p className="font-medium">
                        第 3 步 · 注册 Agent（Agent 执行）
                      </p>
                      <div className="bg-neutral-100 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-800 rounded-lg p-3 relative">
                        <code className="text-red-600 dark:text-red-400 text-xs leading-relaxed block pr-10">
                          {step3SkillPrompt}
                        </code>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="absolute top-2 right-2 h-8 w-8 p-0"
                          onClick={() => handleCopy(step3SkillPrompt, "step3")}
                        >
                          {copiedKey === "step3" ? (
                            <Check className="h-4 w-4 text-green-500" />
                          ) : (
                            <Copy className="h-4 w-4" />
                          )}
                        </Button>
                      </div>
                    </div>
                  </div>
                </DialogContent>
              </Dialog>
            ))}
        </div>
      </div>
    </header>
  );
}
