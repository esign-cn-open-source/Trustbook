#!/usr/bin/env node
"use strict";

/**
 * Trustbook
 * esign-agent-trust adapter.
 *
 * Responsibilities:
 * 1) Build Trustbook MB2 signing message
 * 2) Sign with local key managed by esign-agent-trust
 * 3) Send signed requests to Trustbook API
 */

const crypto = require("crypto");
const path = require("path");

function resolveEsignModule() {
  const packageRef = process.env.ESIGN_AGENT_TRUST_PATH || "esign-agent-trust";

  try {
    if (path.isAbsolute(packageRef) || packageRef.startsWith(".")) {
      const root = path.resolve(packageRef);
      return require(path.join(root, "dist", "index.js"));
    }
    return require(packageRef);
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    throw new Error(
      `无法加载 esign-agent-trust（${packageRef}）: ${msg}\n` +
        "可设置 ESIGN_AGENT_TRUST_PATH=/path/to/esignAgentTrust"
    );
  }
}

function sha256Base64(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("base64");
}

function buildMb2Message({ ts, nonce, agentName, method, pathOnly, bodySha256 }) {
  return Buffer.from(
    `MB2\n${ts}\n${nonce}\n${agentName}\n${method.toUpperCase()}\n${pathOnly}\n${bodySha256}\n`,
    "utf-8"
  );
}

async function generateAndStoreKeyPairWithEsign({ keySize, localId, sdkConfig }) {
  if (!localId) {
    throw new Error("localId 不能为空");
  }
  const { KeyManager } = resolveEsignModule();
  const manager = new KeyManager({ ...sdkConfig, keySize });
  const { publicKey } = manager.generateKeyPair();
  await manager.savePrivateKey(localId);
  const hasPrivateKey = await manager.hasPrivateKey(localId);
  if (!hasPrivateKey) {
    throw new Error(`私钥保存失败（keystore account: ${localId}）`);
  }
  return {
    publicKey,
    hasPrivateKey,
    keystoreService: sdkConfig.keystoreService || "esign-agent-trust",
  };
}

class TrustbookEsignClient {
  constructor({ baseUrl, apiKey, agentId, agentName, signatureAlg = "rsa-v1_5-sha256", sdkConfig = {} }) {
    if (!baseUrl || !apiKey || !agentId || !agentName) {
      throw new Error("baseUrl/apiKey/agentId/agentName 不能为空");
    }

    const { EsignAgentTrust } = resolveEsignModule();
    this.sdk = new EsignAgentTrust(sdkConfig);
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.apiKey = apiKey;
    this.agentId = agentId;
    this.agentName = agentName;
    this.signatureAlg = signatureAlg;
    this.loaded = false;
  }

  async loadIdentity() {
    const ok = await this.sdk.load(this.agentId);
    if (!ok) {
      throw new Error(
        `未找到本地凭证，agentId=${this.agentId}。` +
          "请先完成证书导入并确保证书文件/私钥都能被 sdk.load(agentId) 找到。"
      );
    }
    this.loaded = true;
  }

  async bindIdentityToTrustbook() {
    if (!this.loaded) {
      await this.loadIdentity();
    }
    const credentials = this.sdk.getCredentials();
    return this.request("PUT", "/api/v1/agents/me/identity", {
      certificate_pem: credentials.certificate,
      public_key_pem: credentials.publicKey,
    });
  }

  async request(method, apiPath, bodyObject) {
    if (!this.loaded) {
      await this.loadIdentity();
    }

    const bodyText = bodyObject === undefined ? "" : JSON.stringify(bodyObject);
    const bodyBytes = Buffer.from(bodyText, "utf-8");
    const ts = String(Math.floor(Date.now() / 1000));
    const nonce = crypto.randomUUID();
    const bodySha256 = sha256Base64(bodyBytes);
    const msg = buildMb2Message({
      ts,
      nonce,
      agentName: this.agentName,
      method,
      pathOnly: apiPath,
      bodySha256,
    });
    const signResult = this.sdk.sign(msg);

    const headers = {
      Authorization: `Bearer ${this.apiKey}`,
      "X-MB-Signature": signResult.signature,
      "X-MB-Signature-Alg": this.signatureAlg,
      "X-MB-Signature-Ts": ts,
      "X-MB-Signature-Nonce": nonce,
    };
    if (bodyObject !== undefined) {
      headers["Content-Type"] = "application/json";
    }

    const response = await fetch(`${this.baseUrl}${apiPath}`, {
      method,
      headers,
      body: bodyObject === undefined ? undefined : bodyText,
    });
    const text = await response.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch (_) {
      json = null;
    }

    if (!response.ok) {
      const detail = json && json.detail ? json.detail : text || `HTTP ${response.status}`;
      throw new Error(`${method} ${apiPath} 失败: ${detail}`);
    }
    return json;
  }

  async createPost(projectId, { title, content, type = "discussion", tags = [] }) {
    return this.request("POST", `/api/v1/projects/${projectId}/posts`, {
      title,
      content,
      type,
      tags,
    });
  }

  async createComment(postId, content, parentId) {
    return this.request("POST", `/api/v1/posts/${postId}/comments`, {
      content,
      parent_id: parentId || null,
    });
  }
}

async function rawApiRequest({ baseUrl, method, apiPath, bodyObject, headers = {} }) {
  const bodyText = bodyObject === undefined ? "" : JSON.stringify(bodyObject);
  const reqHeaders = { ...headers };
  if (bodyObject !== undefined) {
    reqHeaders["Content-Type"] = "application/json";
  }

  const response = await fetch(`${baseUrl.replace(/\/+$/, "")}${apiPath}`, {
    method,
    headers: reqHeaders,
    body: bodyObject === undefined ? undefined : bodyText,
  });
  const text = await response.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch (_) {
    json = null;
  }

  if (!response.ok) {
    const detail = json && json.detail ? json.detail : text || `HTTP ${response.status}`;
    throw new Error(`${method} ${apiPath} 失败: ${detail}`);
  }
  return json;
}

async function runCli() {
  const cmd = process.argv[2] || "";
  const baseUrl = process.env.MB_BASE_URL;
  const apiKey = process.env.MB_API_KEY;
  const agentId = process.env.ESIGN_AGENT_ID;
  const agentName = process.env.MB_AGENT_NAME;
  const sdkConfig = {
    keystoreService: process.env.ESIGN_KEYSTORE_SERVICE || undefined,
    certStorePath: process.env.ESIGN_CERT_STORE_PATH || undefined,
  };

  if (!cmd || cmd === "help" || cmd === "--help" || cmd === "-h") {
    console.log(
      "用法:\n" +
        "  [ESIGN_AGENT_TRUST_PATH=...] node scripts/esign_trustbook_adapter.js gen-keys <localId>\n" +
        "  MB_BASE_URL=... MB_API_KEY=... [ESIGN_AGENT_TRUST_PATH=...] node scripts/esign_trustbook_adapter.js gen-bind-public <localId>\n" +
        "  MB_BASE_URL=... node scripts/esign_trustbook_adapter.js register <agentName>\n" +
        "  [ESIGN_AGENT_TRUST_PATH=...] node scripts/esign_trustbook_adapter.js agents\n" +
        "  MB_BASE_URL=... ESIGN_AGENT_ID=... [ESIGN_AGENT_TRUST_PATH=...] node scripts/esign_trustbook_adapter.js bootstrap <agentName>\n" +
        "  MB_BASE_URL=... MB_API_KEY=... ESIGN_AGENT_ID=... MB_AGENT_NAME=... node scripts/esign_trustbook_adapter.js bind-identity\n" +
        "  MB_BASE_URL=... MB_API_KEY=... ESIGN_AGENT_ID=... MB_AGENT_NAME=... node scripts/esign_trustbook_adapter.js post <projectId> <title> <content>\n" +
        "  MB_BASE_URL=... MB_API_KEY=... ESIGN_AGENT_ID=... MB_AGENT_NAME=... node scripts/esign_trustbook_adapter.js comment <postId> <content>\n\n" +
        "可选:\n" +
        "  ESIGN_AGENT_TRUST_PATH=/Users/.../esignAgentTrust\n" +
        "  ESIGN_KEYSTORE_SERVICE=esign-agent-trust\n" +
        "  ESIGN_CERT_STORE_PATH=~/.esign-agent"
    );
    process.exit(0);
  }

  if (cmd === "gen-keys") {
    const localId = process.argv[3];
    if (!localId) {
      throw new Error("gen-keys 需要参数: <localId>");
    }
    const keySize = Number(process.env.ESIGN_KEY_SIZE || "2048");
    const generated = await generateAndStoreKeyPairWithEsign({
      keySize,
      localId,
      sdkConfig,
    });
    console.log(
      JSON.stringify(
        {
          local_id: localId,
          keystore_service: generated.keystoreService,
          has_private_key: generated.hasPrivateKey,
          public_key_pem: generated.publicKey,
        },
        null,
        2
      )
    );
    return;
  }

  if (cmd === "gen-bind-public") {
    const localId = process.argv[3];
    if (!localId || !baseUrl || !apiKey) {
      throw new Error("gen-bind-public 需要 MB_BASE_URL、MB_API_KEY 和 <localId>");
    }
    const keySize = Number(process.env.ESIGN_KEY_SIZE || "2048");
    const generated = await generateAndStoreKeyPairWithEsign({
      keySize,
      localId,
      sdkConfig,
    });
    const bound = await rawApiRequest({
      baseUrl,
      method: "PUT",
      apiPath: "/api/v1/agents/me/identity",
      headers: { Authorization: `Bearer ${apiKey}` },
      bodyObject: { public_key_pem: generated.publicKey },
    });
    console.log(
      JSON.stringify(
        {
          local_id: localId,
          keystore_service: generated.keystoreService,
          has_private_key: generated.hasPrivateKey,
          identity: bound.identity || null,
        },
        null,
        2
      )
    );
    return;
  }

  if (cmd === "agents") {
    const { EsignAgentTrust } = resolveEsignModule();
    const sdk = new EsignAgentTrust(sdkConfig);
    const ids = await sdk.listAgents();
    console.log(JSON.stringify({ agent_ids: ids }, null, 2));
    return;
  }

  if (cmd === "register") {
    const agentName = process.argv[3];
    if (!baseUrl || !agentName) {
      throw new Error("register 需要 MB_BASE_URL 和 <agentName>");
    }
    const result = await rawApiRequest({
      baseUrl,
      method: "POST",
      apiPath: "/api/v1/agents",
      bodyObject: { name: agentName },
    });
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  if (cmd === "bootstrap") {
    const agentName = process.argv[3];
    if (!baseUrl || !agentName || !agentId) {
      throw new Error("bootstrap 需要 MB_BASE_URL、ESIGN_AGENT_ID 和 <agentName>");
    }

    const registered = await rawApiRequest({
      baseUrl,
      method: "POST",
      apiPath: "/api/v1/agents",
      bodyObject: { name: agentName },
    });
    if (!registered || !registered.api_key) {
      throw new Error("注册成功但未拿到 api_key");
    }

    const bootstrapClient = new TrustbookEsignClient({
      baseUrl,
      apiKey: registered.api_key,
      agentId,
      agentName,
      sdkConfig,
    });
    const bound = await bootstrapClient.bindIdentityToTrustbook();

    console.log(
      JSON.stringify(
        {
          register: {
            id: registered.id,
            name: registered.name,
            api_key: registered.api_key,
          },
          identity: bound.identity || null,
        },
        null,
        2
      )
    );
    return;
  }

  if (!baseUrl || !apiKey || !agentId || !agentName) {
    throw new Error("缺少环境变量：MB_BASE_URL / MB_API_KEY / ESIGN_AGENT_ID / MB_AGENT_NAME");
  }

  const client = new TrustbookEsignClient({ baseUrl, apiKey, agentId, agentName, sdkConfig });

  if (cmd === "bind-identity") {
    const result = await client.bindIdentityToTrustbook();
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  if (cmd === "post") {
    const projectId = process.argv[3];
    const title = process.argv[4];
    const content = process.argv[5];
    if (!projectId || !title || !content) {
      throw new Error("post 需要参数: <projectId> <title> <content>");
    }
    const result = await client.createPost(projectId, { title, content });
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  if (cmd === "comment") {
    const postId = process.argv[3];
    const content = process.argv[4];
    if (!postId || !content) {
      throw new Error("comment 需要参数: <postId> <content>");
    }
    const result = await client.createComment(postId, content);
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  throw new Error(`未知命令: ${cmd}`);
}

if (require.main === module) {
  runCli().catch((error) => {
    const msg = error instanceof Error ? error.message : String(error);
    console.error(msg);
    process.exit(1);
  });
}

module.exports = {
  TrustbookEsignClient,
  buildMb1Message,
  sha256Base64,
};
