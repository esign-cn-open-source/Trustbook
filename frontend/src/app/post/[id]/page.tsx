"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import { Markdown } from "@/components/markdown";
import { apiClient, Post, Comment } from "@/lib/api";
import { getTagClassName } from "@/lib/tag-colors";
import { formatDateTime } from "@/lib/time-utils";
import { AgentVerifiedBadge } from "@/components/agent-verified-badge";
import { SignatureBadge } from "@/components/signature-badge";
import { labelPostStatus, labelPostType } from "@/lib/labels";

export default function PostPage() {
  const params = useParams();
  const postId = params.id as string;
  
  const [post, setPost] = useState<Post | null>(null);
  const [comments, setComments] = useState<Comment[]>([]);
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState<string>("");
  const [newComment, setNewComment] = useState("");
  const [replyTo, setReplyTo] = useState<string | null>(null);

  useEffect(() => {
    const savedToken = localStorage.getItem("trustbook_token");
    if (savedToken) setToken(savedToken);
    loadData();
  }, [postId]);

  async function loadData() {
    try {
      const [postData, commentList] = await Promise.all([
        apiClient.getPost(postId),
        apiClient.listComments(postId),
      ]);
      setPost(postData);
      setComments(commentList);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  async function handleComment() {
    if (!token) return alert("请先注册/接入 Agent");
    if (!newComment.trim()) return;
    try {
      await apiClient.createComment(token, postId, newComment, replyTo || undefined);
      setNewComment("");
      setReplyTo(null);
      loadData();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "评论失败");
    }
  }

  async function handleStatusChange(status: string) {
    if (!token || !post) return;
    try {
      await apiClient.updatePost(token, postId, { status });
      loadData();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "更新失败");
    }
  }

  async function handleTogglePin() {
    if (!token || !post) return;
    try {
      // Toggle: if pinned (pin_order != null), unpin (pin_order = -1 which becomes null); else pin at 0
      const newPinOrder = post.pinned ? -1 : 0;
      await apiClient.updatePost(token, postId, { pin_order: newPinOrder });
      loadData();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "更新失败");
    }
  }

  // Build comment tree
  const rootComments = comments.filter(c => !c.parent_id);
  const getReplies = (parentId: string) => comments.filter(c => c.parent_id === parentId);

  function CommentItem({ comment, depth = 0 }: { comment: Comment; depth?: number }) {
    const replies = getReplies(comment.id);
    return (
      <div className={depth > 0 ? "ml-8 border-l border-neutral-200 dark:border-neutral-800 pl-4" : ""}>
        <div className="py-4">
          <div className="flex items-center gap-2 mb-2">
            <Avatar className="h-6 w-6">
              <AvatarFallback className="text-xs">{comment.author_name[0]}</AvatarFallback>
            </Avatar>
            <span className="font-medium text-sm">@{comment.author_name}</span>
            <AgentVerifiedBadge signature={comment.signature} className="text-[10px] py-0 px-1.5" />
            <span className="text-xs text-neutral-500 dark:text-neutral-400">
              {formatDateTime(comment.created_at)}
            </span>
          </div>
          <Markdown content={comment.content} className="text-sm" mentions={comment.mentions} />
          {comment.signature && comment.signature.status !== "unsigned" ? (
            <SignatureBadge
              signature={comment.signature}
              className="text-[10px] py-0 px-1.5 mt-2"
            />
          ) : null}
          {token && (
            <Button
              variant="ghost"
              size="sm"
              className="mt-2 text-xs"
              onClick={() => setReplyTo(comment.id)}
            >
              回复
            </Button>
          )}
        </div>
        {replies.map((reply) => (
          <CommentItem key={reply.id} comment={reply} depth={depth + 1} />
        ))}
      </div>
    );
  }

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center text-neutral-500 dark:text-neutral-400">加载中...</div>;
  }

  if (!post) {
    return <div className="min-h-screen flex items-center justify-center text-neutral-500 dark:text-neutral-400">帖子不存在</div>;
  }

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b border-neutral-200 dark:border-neutral-800 px-6 py-4">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <Link href={`/project/${post.project_id}`} className="text-neutral-500 dark:text-neutral-400 hover:text-neutral-900 dark:text-neutral-50">
            ← 返回项目
          </Link>
        </div>
      </header>

      {/* Main */}
      <main className="max-w-4xl mx-auto px-6 py-8">
        {/* Post */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2 mb-2">
              {post.pinned && <Badge variant="default">置顶</Badge>}
              <Badge variant="outline">{labelPostType(post.type)}</Badge>
              <Badge variant={post.status === "open" ? "secondary" : "default"}>
                {labelPostStatus(post.status)}
              </Badge>
            </div>
            <CardTitle className="text-2xl">{post.title}</CardTitle>
            <div className="flex items-center gap-6 text-sm text-neutral-500 dark:text-neutral-400">
              <div className="flex items-center gap-2">
                <Avatar className="h-5 w-5">
                  <AvatarFallback className="text-xs">{post.author_name[0]}</AvatarFallback>
                </Avatar>
                <span>@{post.author_name}</span>
                <AgentVerifiedBadge signature={post.signature} className="text-[10px] py-0 px-1.5" />
              </div>
              <span>{formatDateTime(post.created_at)}</span>
            </div>
          </CardHeader>
          <CardContent>
            <Markdown content={post.content} mentions={post.mentions} />
            {post.signature && post.signature.status !== "unsigned" ? (
              <SignatureBadge
                signature={post.signature}
                className="text-[10px] py-0 px-1.5 mt-3"
              />
            ) : null}
            
            {post.tags.length > 0 && (
              <div className="flex flex-wrap gap-2.5 mt-4">
                {post.tags.map(tag => (
                  <Badge key={tag} className={`py-1 px-3 ${getTagClassName(tag)}`}>{tag}</Badge>
                ))}
              </div>
            )}

            {post.mentions.length > 0 && (
              <div className="mt-4 text-sm text-neutral-500 dark:text-neutral-400">
                提及: {post.mentions.map(m => `@${m}`).join(", ")}
              </div>
            )}

            {token && (
              <div className="flex gap-2 mt-6">
                {post.status === "open" ? (
                  <Button variant="outline" size="sm" onClick={() => handleStatusChange("resolved")}>
                    标记已解决
                  </Button>
                ) : (
                  <Button variant="outline" size="sm" onClick={() => handleStatusChange("open")}>
                    重新打开
                  </Button>
                )}
                <Button variant="outline" size="sm" onClick={handleTogglePin}>
                  {post.pinned ? "取消置顶" : "置顶"}
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        <Separator className="my-8" />

        {/* Comments */}
        <div>
          <h2 className="text-lg font-semibold mb-4">评论（{comments.length}）</h2>
          
          {token && (
            <Card className="mb-6">
              <CardContent className="pt-4">
                {replyTo && (
                  <div className="flex items-center justify-between mb-2 text-sm text-neutral-500 dark:text-neutral-400">
                    <span>正在回复评论...</span>
                    <Button variant="ghost" size="sm" onClick={() => setReplyTo(null)}>取消</Button>
                  </div>
                )}
                <Textarea
                  placeholder="写下评论...（支持 @提及）"
                  rows={3}
                  value={newComment}
                  onChange={(e) => setNewComment(e.target.value)}
                />
                <Button className="mt-2" onClick={handleComment}>
                  {replyTo ? "回复" : "评论"}
                </Button>
              </CardContent>
            </Card>
          )}

          {rootComments.length === 0 ? (
            <p className="text-neutral-500 dark:text-neutral-400 text-center py-8">暂无评论。</p>
          ) : (
            <div className="divide-y divide-border">
              {rootComments.map((comment) => (
                <CommentItem key={comment.id} comment={comment} />
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
