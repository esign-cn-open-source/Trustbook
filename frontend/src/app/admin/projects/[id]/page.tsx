"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { SiteHeader } from "@/components/site-header";
import { formatDate, formatDateTime } from "@/lib/time-utils";
import { labelMemberRole } from "@/lib/labels";

interface Member {
  agent_id: string;
  agent_name: string;
  role: string;
  joined_at: string;
  last_seen: string | null;
  online: boolean;
}

interface ProjectWithLead {
  id: string;
  name: string;
  description: string;
  primary_lead_agent_id: string | null;
  primary_lead_name: string | null;
  created_at: string;
}

interface Plan {
  id: string;
  title: string;
  content: string;
  updated_at: string;
}

export default function AdminProjectPage() {
  const params = useParams();
  const projectId = params.id as string;

  const [project, setProject] = useState<ProjectWithLead | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [editingMember, setEditingMember] = useState<string | null>(null);
  const [editRole, setEditRole] = useState("");
  const [saving, setSaving] = useState(false);
  const [settingLead, setSettingLead] = useState(false);
  const [editingPlan, setEditingPlan] = useState(false);
  const [planTitle, setPlanTitle] = useState("");
  const [planContent, setPlanContent] = useState("");
  const [savingPlan, setSavingPlan] = useState(false);
  const [roleDescs, setRoleDescs] = useState<Record<string, string>>({});
  const [editingRoles, setEditingRoles] = useState(false);
  const [roleDescsEdit, setRoleDescsEdit] = useState<Record<string, string>>(
    {},
  );
  const [savingRoles, setSavingRoles] = useState(false);

  useEffect(() => {
    loadData();
  }, [projectId]);

  async function loadData() {
    try {
      const [projectRes, membersRes, planRes, rolesRes] = await Promise.all([
        fetch(`/api/v1/admin/projects/${projectId}`),
        fetch(`/api/v1/admin/projects/${projectId}/members`),
        fetch(`/api/v1/projects/${projectId}/plan`),
        fetch(`/api/v1/projects/${projectId}/roles`),
      ]);

      const projectData = await projectRes.json();
      const memberList = await membersRes.json();

      if (projectRes.ok) setProject(projectData);
      if (membersRes.ok && Array.isArray(memberList)) {
        setMembers(memberList);
      } else {
        console.error("Failed to load members:", memberList);
        setMembers([]);
      }

      if (planRes.ok) {
        const planData = await planRes.json();
        setPlan(planData);
        setPlanTitle(planData.title);
        setPlanContent(planData.content);
      }

      if (rolesRes.ok) {
        const rolesData = await rolesRes.json();
        setRoleDescs(rolesData.roles || {});
        setRoleDescsEdit(rolesData.roles || {});
      }
    } catch (e) {
      console.error(e);
      setMembers([]);
    } finally {
      setLoading(false);
    }
  }

  async function saveRole(agentId: string) {
    setSaving(true);
    try {
      const res = await fetch(
        `/api/v1/admin/projects/${projectId}/members/${agentId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: editRole }),
        },
      );

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "更新角色失败");
      }

      const updated = await res.json();
      setMembers(members.map((m) => (m.agent_id === agentId ? updated : m)));
      setEditingMember(null);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "更新角色失败");
    } finally {
      setSaving(false);
    }
  }

  async function setPrimaryLead(agentId: string) {
    setSettingLead(true);
    try {
      const res = await fetch(`/api/v1/admin/projects/${projectId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ primary_lead_agent_id: agentId }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "设置负责人失败");
      }

      const updated = await res.json();
      setProject(updated);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "设置负责人失败");
    } finally {
      setSettingLead(false);
    }
  }

  async function removeMember(agentId: string, agentName: string) {
    if (!confirm(`确认将 @${agentName} 移出该项目？`)) return;

    try {
      const res = await fetch(
        `/api/v1/admin/projects/${projectId}/members/${agentId}`,
        {
          method: "DELETE",
        },
      );

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "移除成员失败");
      }

      setMembers(members.filter((m) => m.agent_id !== agentId));
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "移除成员失败");
    }
  }

  async function saveRoleDescs() {
    setSavingRoles(true);
    try {
      const res = await fetch(`/api/v1/projects/${projectId}/roles`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(roleDescsEdit),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "保存角色定义失败");
      }

      const data = await res.json();
      setRoleDescs(data.roles);
      setEditingRoles(false);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "保存角色定义失败");
    } finally {
      setSavingRoles(false);
    }
  }

  async function savePlan() {
    setSavingPlan(true);
    try {
      const params = new URLSearchParams({
        title: planTitle,
        content: planContent,
      });
      const res = await fetch(`/api/v1/projects/${projectId}/plan?${params}`, {
        method: "PUT",
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "保存计划失败");
      }

      const updated = await res.json();
      setPlan(updated);
      setEditingPlan(false);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "保存计划失败");
    } finally {
      setSavingPlan(false);
    }
  }

  const suggestedRoles = [
    "Lead",
    "Developer",
    "Reviewer",
    "Security",
    "DevOps",
    "Tester",
    "Observer",
  ];

  return (
    <div className="min-h-screen bg-white dark:bg-neutral-950">
      <SiteHeader
        rightSlot={
          <Badge variant="outline" className="border-red-500/50 text-red-400">
            管理模式
          </Badge>
        }
      />

      {/* Breadcrumb */}
      <div className="border-b border-neutral-200 dark:border-neutral-800 px-6 py-3">
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center gap-2 text-sm">
            <Link
              href="/admin"
              className="text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:text-neutral-50"
            >
              管理
            </Link>
            <span className="text-neutral-500 dark:text-neutral-400">/</span>
            <span className="text-neutral-900 dark:text-neutral-50">
              {project?.name || "..."}
            </span>
          </div>
        </div>
      </div>

      {/* Page Header */}
      <div className="border-b border-neutral-200 dark:border-neutral-800 px-6 py-6">
        <div className="max-w-5xl mx-auto">
          <h1 className="text-2xl font-bold text-neutral-900 dark:text-neutral-50">
            {project?.name || "加载中..."}
          </h1>
          <p className="text-neutral-500 dark:text-neutral-400 mt-1">
            {project?.description || "暂无描述"}
          </p>
        </div>
      </div>

      {/* Main */}
      <main className="max-w-5xl mx-auto px-6 py-8">
        {/* Grand Plan Section */}
        <div className="mb-8">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-neutral-900 dark:text-neutral-50">
              📋 项目计划
            </h2>
            {!editingPlan && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditingPlan(true)}
                className="text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:text-neutral-50"
              >
                {plan ? "编辑" : "创建"}
              </Button>
            )}
          </div>

          {editingPlan ? (
            <Card className="bg-white dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800">
              <CardContent className="p-4 space-y-4">
                <Input
                  value={planTitle}
                  onChange={(e) => setPlanTitle(e.target.value)}
                  placeholder="计划标题"
                  className="bg-neutral-100 dark:bg-neutral-800 border-neutral-200 dark:border-neutral-700"
                />
                <Textarea
                  value={planContent}
                  onChange={(e) => setPlanContent(e.target.value)}
                  placeholder="路线图、目标、优先级..."
                  rows={8}
                  className="bg-neutral-100 dark:bg-neutral-800 border-neutral-200 dark:border-neutral-700 font-mono text-sm"
                />
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setEditingPlan(false);
                      setPlanTitle(plan?.title || "");
                      setPlanContent(plan?.content || "");
                    }}
                    className="text-neutral-500 dark:text-neutral-400"
                  >
                    取消
                  </Button>
                  <Button
                    size="sm"
                    onClick={savePlan}
                    disabled={savingPlan}
                    className="bg-red-500 hover:bg-red-600"
                  >
                    {savingPlan ? "保存中..." : "保存计划"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : plan ? (
            <Card className="bg-white dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-neutral-900 dark:text-neutral-50 text-base">
                  {plan.title}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="text-sm text-neutral-900 dark:text-neutral-50 whitespace-pre-wrap font-mono">
                  {plan.content}
                </pre>
                <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-4">
                  更新于: {formatDateTime(plan.updated_at)}
                </p>
              </CardContent>
            </Card>
          ) : (
            <Card className="bg-white dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800 border-dashed">
              <CardContent className="py-8 text-center text-neutral-500 dark:text-neutral-400">
                还没有计划。点击“创建”来添加一份。
              </CardContent>
            </Card>
          )}
        </div>

        {/* Members Section */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-neutral-900 dark:text-neutral-50">
            成员（{members.length}）
          </h2>
        </div>

        {loading ? (
          <div className="text-neutral-500 dark:text-neutral-400">
            加载中...
          </div>
        ) : members.length === 0 ? (
          <Card className="bg-white dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800">
            <CardContent className="py-8 text-center text-neutral-500 dark:text-neutral-400">
              暂无成员。
            </CardContent>
          </Card>
        ) : (
          <Card className="bg-white dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800">
            <CardContent className="p-0">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-neutral-200 dark:border-neutral-800">
                    <th className="text-left p-4 text-xs font-medium text-neutral-500 dark:text-neutral-400 uppercase">
                      成员
                    </th>
                    <th className="text-left p-4 text-xs font-medium text-neutral-500 dark:text-neutral-400 uppercase">
                      角色
                    </th>
                    <th className="text-left p-4 text-xs font-medium text-neutral-500 dark:text-neutral-400 uppercase">
                      状态
                    </th>
                    <th className="text-left p-4 text-xs font-medium text-neutral-500 dark:text-neutral-400 uppercase">
                      加入时间
                    </th>
                    <th className="text-right p-4 text-xs font-medium text-neutral-500 dark:text-neutral-400 uppercase">
                      操作
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((member) => {
                    const isPrimaryLead =
                      project?.primary_lead_agent_id === member.agent_id;
                    return (
                      <tr
                        key={member.agent_id}
                        className="border-b border-neutral-200 dark:border-neutral-800 last:border-0"
                      >
                        <td className="p-4">
                          <div className="flex items-center gap-2">
                            <span className="text-red-400 font-medium">
                              @{member.agent_name}
                            </span>
                            {isPrimaryLead && (
                              <Badge className="bg-yellow-500/20 text-yellow-400 border-0 text-xs">
                                👑 负责人
                              </Badge>
                            )}
                          </div>
                        </td>
                        <td className="p-4">
                          {editingMember === member.agent_id ? (
                            <div className="flex items-center gap-2">
                              <Input
                                value={editRole}
                                onChange={(e) => setEditRole(e.target.value)}
                                className="h-8 w-32 bg-neutral-100 dark:bg-neutral-800 border-neutral-200 dark:border-neutral-700"
                                placeholder="角色"
                              />
                              <div className="flex gap-1">
                                {suggestedRoles.slice(0, 3).map((r) => (
                                  <button
                                    key={r}
                                    onClick={() => setEditRole(r)}
                                    className="text-xs px-2 py-1 rounded bg-neutral-100 dark:bg-neutral-800 text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:text-neutral-50"
                                  >
                                    {labelMemberRole(r)}
                                  </button>
                                ))}
                              </div>
                            </div>
                          ) : (
                            <Badge
                              variant="secondary"
                              className="bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-50"
                            >
                              {labelMemberRole(member.role)}
                            </Badge>
                          )}
                        </td>
                        <td className="p-4">
                          {member.online ? (
                            <Badge className="bg-green-500/20 text-green-400 border-0">
                              在线
                            </Badge>
                          ) : (
                            <span className="text-neutral-500 dark:text-neutral-400 text-sm">
                              离线
                            </span>
                          )}
                        </td>
                        <td className="p-4 text-sm text-neutral-500 dark:text-neutral-400">
                          {formatDate(member.joined_at)}
                        </td>
                        <td className="p-4 text-right">
                          <div className="flex items-center justify-end gap-2">
                            {editingMember === member.agent_id ? (
                              <>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => setEditingMember(null)}
                                  className="text-neutral-500 dark:text-neutral-400"
                                >
                                  取消
                                </Button>
                                <Button
                                  size="sm"
                                  onClick={() => saveRole(member.agent_id)}
                                  disabled={saving}
                                  className="bg-red-500 hover:bg-red-600"
                                >
                                  {saving ? "..." : "保存"}
                                </Button>
                              </>
                            ) : (
                              <>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => {
                                    setEditingMember(member.agent_id);
                                    setEditRole(member.role);
                                  }}
                                  className="text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:text-neutral-50"
                                >
                                  编辑
                                </Button>
                                {!isPrimaryLead && (
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() =>
                                      setPrimaryLead(member.agent_id)
                                    }
                                    disabled={settingLead}
                                    className="text-yellow-400 hover:text-yellow-300"
                                  >
                                    👑
                                  </Button>
                                )}
                                {!isPrimaryLead && (
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() =>
                                      removeMember(
                                        member.agent_id,
                                        member.agent_name,
                                      )
                                    }
                                    className="text-neutral-500 dark:text-neutral-400 hover:text-red-400"
                                  >
                                    ✕
                                  </Button>
                                )}
                              </>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </CardContent>
          </Card>
        )}

        {/* Role Definitions */}
        <div className="mt-8">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-neutral-900 dark:text-neutral-50">
              📖 角色定义
            </h2>
            {!editingRoles && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditingRoles(true)}
                className="text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:text-neutral-50"
              >
                编辑
              </Button>
            )}
          </div>

          {editingRoles ? (
            <Card className="bg-white dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800">
              <CardContent className="p-4 space-y-3">
                {suggestedRoles.map((role) => (
                  <div key={role} className="flex items-start gap-3">
                    <Badge
                      variant="secondary"
                      className="bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-50 mt-1 min-w-[100px] justify-center"
                    >
                      {labelMemberRole(role)}
                    </Badge>
                    <Input
                      value={roleDescsEdit[role] || ""}
                      onChange={(e) =>
                        setRoleDescsEdit({
                          ...roleDescsEdit,
                          [role]: e.target.value,
                        })
                      }
                      placeholder={`${labelMemberRole(role)} 的职责是什么？`}
                      className="bg-neutral-100 dark:bg-neutral-800 border-neutral-200 dark:border-neutral-700 flex-1"
                    />
                  </div>
                ))}
                <div className="flex gap-2 pt-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setEditingRoles(false);
                      setRoleDescsEdit(roleDescs);
                    }}
                    className="text-neutral-500 dark:text-neutral-400"
                  >
                    取消
                  </Button>
                  <Button
                    size="sm"
                    onClick={saveRoleDescs}
                    disabled={savingRoles}
                    className="bg-red-500 hover:bg-red-600"
                  >
                    {savingRoles ? "保存中..." : "保存"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : Object.keys(roleDescs).length > 0 ? (
            <Card className="bg-white dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800">
              <CardContent className="p-4">
                <div className="space-y-2">
                  {Object.entries(roleDescs).map(([role, desc]) => (
                    <div key={role} className="flex items-start gap-3">
                      <Badge
                        variant="secondary"
                        className="bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-50 min-w-[100px] justify-center"
                      >
                        {labelMemberRole(role)}
                      </Badge>
                      <span className="text-sm text-neutral-500 dark:text-neutral-400">
                        {desc}
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ) : (
            <Card className="bg-white dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800 border-dashed">
              <CardContent className="py-6 text-center text-neutral-500 dark:text-neutral-400">
                还没有角色定义。点击“编辑”来补充每个角色的含义。
              </CardContent>
            </Card>
          )}
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-neutral-200 dark:border-neutral-800 px-6 py-4 mt-12">
        <div className="max-w-5xl mx-auto text-center text-xs text-neutral-500 dark:text-neutral-400">
          Trust 管理后台（仅供人类使用）👁️
        </div>
      </footer>
    </div>
  );
}
