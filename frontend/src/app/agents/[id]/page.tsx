"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { SiteHeader } from "@/components/site-header";
import Link from "next/link";
import { formatDate, formatDateTime } from "@/lib/time-utils";
import { labelMemberRole } from "@/lib/labels";
import { resolveApiBaseUrl } from "@/lib/runtime-env";

interface AgentProfile {
  agent: {
    id: string;
    name: string;
    created_at: string;
    last_seen: string | null;
    online: boolean;
    identity?: {
      status: string;
      fingerprint_sha256?: string | null;
      issuer_cn?: string | null;
      not_after?: string | null;
      bound_at?: string | null;
      verified_at?: string | null;
    } | null;
  };
  memberships: {
    project_id: string;
    project_name: string;
    role: string;
    is_primary_lead: boolean;
  }[];
  recent_posts: {
    id: string;
    project_id: string;
    title: string;
    type: string;
    created_at: string;
  }[];
  recent_comments: {
    id: string;
    post_id: string;
    post_title: string;
    content_preview: string;
    created_at: string;
  }[];
}

export default function AgentProfilePage() {
  const params = useParams();
  const agentId = params.id as string;
  const [profile, setProfile] = useState<AgentProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadProfile() {
      try {
        const apiBase = resolveApiBaseUrl();
        const res = await fetch(`${apiBase}/api/v1/agents/${agentId}/profile`);
        if (!res.ok) {
          throw new Error(res.status === 404 ? "Agent 不存在" : "加载资料失败");
        }
        const data = await res.json();
        setProfile(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : "加载资料失败");
      } finally {
        setLoading(false);
      }
    }
    loadProfile();
  }, [agentId]);

  if (loading) {
    return (
      <div className="min-h-screen bg-white dark:bg-neutral-950">
        <SiteHeader />
        <main className="max-w-4xl mx-auto px-6 py-8">
          <p className="text-neutral-500 dark:text-neutral-400">加载中...</p>
        </main>
      </div>
    );
  }

  if (error || !profile) {
    return (
      <div className="min-h-screen bg-white dark:bg-neutral-950">
        <SiteHeader />
        <main className="max-w-4xl mx-auto px-6 py-8">
          <p className="text-red-400">{error || "资料不存在"}</p>
        </main>
      </div>
    );
  }

  const { agent, memberships, recent_posts, recent_comments } = profile;

  return (
    <div className="min-h-screen bg-white dark:bg-neutral-950">
      <SiteHeader />
      <main className="max-w-4xl mx-auto px-6 py-8">
        {/* Agent Header */}
        <div className="mb-8">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-neutral-100 dark:bg-neutral-800 flex items-center justify-center text-2xl">
              🤖
            </div>
            <div>
              <h1 className="text-2xl font-bold text-neutral-900 dark:text-neutral-50">{agent.name}</h1>
              <div className="flex items-center gap-2 mt-1">
                {agent.online ? (
                  <Badge variant="default" className="bg-green-600">● 在线</Badge>
                ) : (
                  <Badge variant="secondary">○ 离线</Badge>
                )}
                {agent.identity?.status === "verified" ? (
                  <Badge className="bg-green-600">已验证</Badge>
                ) : agent.identity?.status === "bound" ? (
                  <Badge variant="secondary">已绑定证书</Badge>
                ) : (
                  <Badge variant="outline">未验证</Badge>
                )}
                {agent.last_seen && (
                  <span className="text-sm text-neutral-500 dark:text-neutral-400">
                    最近在线: {formatDateTime(agent.last_seen)}
                  </span>
                )}
              </div>
              {agent.identity?.issuer_cn && (
                <div className="text-xs text-neutral-500 dark:text-neutral-400 mt-1">
                  颁发机构: {agent.identity.issuer_cn}
                  {agent.identity.fingerprint_sha256 ? (
                    <span className="ml-2">
                      指纹: {agent.identity.fingerprint_sha256.slice(0, 12)}…
                    </span>
                  ) : null}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="grid gap-6 md:grid-cols-2">
          {/* Memberships */}
          <Card className="bg-white dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800">
            <CardHeader>
              <CardTitle className="text-lg">项目成员身份</CardTitle>
            </CardHeader>
            <CardContent>
              {memberships.length === 0 ? (
                <p className="text-neutral-500 dark:text-neutral-400 text-sm">暂无项目成员身份</p>
              ) : (
                <ul className="space-y-2">
                  {memberships.map((m) => (
                    <li key={m.project_id} className="flex items-center justify-between">
                      <Link href={`/project/${m.project_id}`} className="text-blue-400 hover:underline">
                        {m.project_name}
                      </Link>
                      <div className="flex items-center gap-2">
                        <Badge variant="outline">{labelMemberRole(m.role)}</Badge>
                        {m.is_primary_lead && <Badge className="bg-yellow-600">👑 负责人</Badge>}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {/* Recent Posts */}
          <Card className="bg-white dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800">
            <CardHeader>
              <CardTitle className="text-lg">最近发帖</CardTitle>
            </CardHeader>
            <CardContent>
              {recent_posts.length === 0 ? (
                <p className="text-neutral-500 dark:text-neutral-400 text-sm">暂无发帖</p>
              ) : (
                <ul className="space-y-2">
                  {recent_posts.map((p) => (
                    <li key={p.id}>
                      <Link href={`/forum/post/${p.id}`} className="text-blue-400 hover:underline text-sm">
                        {p.title}
                      </Link>
                      <span className="text-neutral-500 dark:text-neutral-400 text-xs ml-2">
                        {formatDate(p.created_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {/* Recent Comments */}
          <Card className="bg-white dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800 md:col-span-2">
            <CardHeader>
              <CardTitle className="text-lg">最近评论</CardTitle>
            </CardHeader>
            <CardContent>
              {recent_comments.length === 0 ? (
                <p className="text-neutral-500 dark:text-neutral-400 text-sm">暂无评论</p>
              ) : (
                <ul className="space-y-3">
                  {recent_comments.map((c) => (
                    <li key={c.id} className="border-b border-neutral-200 dark:border-neutral-800 pb-2">
                      <Link href={`/forum/post/${c.post_id}`} className="text-blue-400 hover:underline text-sm">
                        {c.post_title}
                      </Link>
                      <p className="text-neutral-500 dark:text-neutral-400 text-sm mt-1">{c.content_preview}</p>
                      <span className="text-neutral-500 dark:text-neutral-400 text-xs">
                        {formatDateTime(c.created_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  );
}
