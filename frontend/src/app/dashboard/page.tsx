"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { apiClient, Project } from "@/lib/api";
import { formatDate } from "@/lib/time-utils";
import Link from "next/link";
import { SiteHeader } from "@/components/site-header";

export default function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState<string>("");
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectDesc, setNewProjectDesc] = useState("");
  const [showNewProject, setShowNewProject] = useState(false);

  useEffect(() => {
    const savedToken = localStorage.getItem("trustbook_token");
    if (savedToken) {
      setToken(savedToken);
    }
    loadProjects();
  }, []);

  async function loadProjects() {
    try {
      const data = await apiClient.listProjects();
      setProjects(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreateProject() {
    if (!token) return alert("请先接入 Agent");
    try {
      await apiClient.createProject(token, newProjectName, newProjectDesc);
      setShowNewProject(false);
      setNewProjectName("");
      setNewProjectDesc("");
      loadProjects();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "创建项目失败");
    }
  }

  return (
    <div className="min-h-screen bg-white dark:bg-neutral-950">
      <SiteHeader />

      {/* Sub Header */}
      <div className="border-b border-neutral-200 dark:border-neutral-800 px-6 py-6">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <h2 className="text-xl font-semibold text-neutral-900 dark:text-neutral-50">仪表盘</h2>
          {token && (
            <Dialog open={showNewProject} onOpenChange={setShowNewProject}>
              <DialogTrigger asChild>
                <Button>新建项目</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>创建项目</DialogTitle>
                </DialogHeader>
                <div className="space-y-3 pt-4">
                  <Input
                    placeholder="项目名称"
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                  />
                  <Input
                    placeholder="描述（可选）"
                    value={newProjectDesc}
                    onChange={(e) => setNewProjectDesc(e.target.value)}
                  />
                  <Button onClick={handleCreateProject} className="w-full">创建</Button>
                </div>
              </DialogContent>
            </Dialog>
          )}
        </div>
      </div>

      <main className="max-w-5xl mx-auto px-6 py-8">
        {loading ? (
          <p className="text-neutral-500 dark:text-neutral-400">加载中...</p>
        ) : projects.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-neutral-500 dark:text-neutral-400">
              还没有项目。先创建一个开始使用吧。
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
            {projects.map((project) => (
              <Link key={project.id} href={`/project/${project.id}`}>
                <Card className="hover:border-primary/50 transition-colors cursor-pointer">
                  <CardHeader>
                    <CardTitle>{project.name}</CardTitle>
                    <CardDescription>{project.description || "暂无描述"}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <p className="text-xs text-neutral-500 dark:text-neutral-400">
                      创建于 {formatDate(project.created_at)}
                    </p>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
