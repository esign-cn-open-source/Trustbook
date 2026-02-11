export function labelPostStatus(status: string): string {
  switch (status) {
    case "open":
      return "进行中";
    case "resolved":
      return "已解决";
    case "closed":
      return "已关闭";
    default:
      return status;
  }
}

export function labelPostType(type: string): string {
  switch (type) {
    case "discussion":
      return "讨论";
    case "review":
      return "评审";
    case "question":
      return "提问";
    case "announcement":
      return "公告";
    default:
      return type;
  }
}

export function labelSignatureStatus(status: string): string {
  switch (status) {
    case "verified":
      return "已验签";
    case "invalid":
      return "签名异常";
    case "no_cert":
      return "无证书";
    case "cert_expired":
      return "证书过期";
    case "cert_not_yet_valid":
      return "证书未生效";
    case "cert_invalid":
      return "证书无效";
    case "unsigned":
      return "未签名";
    default:
      return status;
  }
}

export function labelNotificationType(type: string): string {
  switch (type) {
    case "mention":
      return "提及";
    case "reply":
      return "回复";
    case "status_change":
      return "状态变更";
    default:
      return type;
  }
}

export function labelMemberRole(role: string): string {
  const v = role.trim();
  if (!v) return role;
  switch (v.toLowerCase()) {
    case "lead":
      return "负责人";
    case "member":
      return "成员";
    case "developer":
      return "开发";
    case "reviewer":
      return "评审";
    case "security":
    case "security-auditor":
      return "安全";
    case "devops":
      return "运维";
    case "tester":
      return "测试";
    case "observer":
      return "观察者";
    default:
      return role;
  }
}
