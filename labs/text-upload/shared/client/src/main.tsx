import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { atom, useAtom } from 'jotai';
import { Button } from './components/ui/button';
import {
  createUploadController,
  type UploadClientState,
  type UploadController,
} from './controller.mjs';
import {
  identities,
  noticeText,
  type DocumentMetadata,
  type IdentityId,
  type NoticeCode,
} from './protocol.mjs';
import './style.css';

function Metadata({ document }: { document: DocumentMetadata }) {
  return (
    <dl className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
      <dt className="text-stone-500">文档 ID</dt>
      <dd className="plain-text font-mono">{document.id}</dd>
      <dt className="text-stone-500">显示名</dt>
      <dd className="plain-text">{document.filename}</dd>
      <dt className="text-stone-500">媒体类型</dt>
      <dd className="plain-text">{document.media_type}</dd>
      <dt className="text-stone-500">原始大小</dt>
      <dd>{document.size_bytes} bytes</dd>
      <dt className="text-stone-500">SHA-256</dt>
      <dd className="plain-text font-mono text-xs leading-6">{document.sha256}</dd>
    </dl>
  );
}

function Notice({ code }: { code: NoticeCode | null }) {
  return code ? (
    <p role="alert" className="plain-text mt-3 text-sm leading-6 text-amber-900">
      {noticeText(code)}
    </p>
  ) : null;
}

function App() {
  const [snapshotAtom] = useState(() => atom<UploadClientState | null>(null));
  const [state, setSnapshot] = useAtom(snapshotAtom);
  const controller = useRef<UploadController | null>(null);

  useEffect(() => {
    // StrictMode 每次 setup 都有自己的 controller；dispose 后不再复用。
    const current = createUploadController();
    controller.current = current;
    setSnapshot(current.getSnapshot());
    const unsubscribe = current.subscribe(() => setSnapshot(current.getSnapshot()));
    current.start();
    return () => {
      unsubscribe();
      if (controller.current === current) controller.current = null;
      current.dispose();
    };
  }, [setSnapshot]);

  const uploadBusy = state ? ['reading', 'hashing', 'posting'].includes(state.upload.phase) : false;
  const observedBytes = state?.listing.documents.reduce((sum, item) => sum + item.size_bytes, 0);
  const uploadProgress = {
    idle: '选择文件后点击上传，不会自动发送。',
    reading: '正在读取文件，浏览器尚未发送。',
    hashing: '正在核对原字节摘要，浏览器尚未发送。',
    posting: '正在等待上传结果…',
    confirmed: '本次上传已确认。',
    rejected: '本次上传被拒绝。',
    unconfirmed: '本次上传结果未确认。',
    stopped: '已停止本次等待。',
    local_error: '文件尚未发送。',
  };

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6 sm:py-12">
      <header className="space-y-3">
        <p className="text-sm font-semibold tracking-wide text-lime-800">本地实验 · 原始字节</p>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">受限文本上传实验</h1>
        <p className="max-w-3xl text-sm leading-7 text-stone-600 sm:text-base">
          选择文件，上传后核对字节，再切换身份查看自己的列表。公开假会话仅用于本地教学，无模型调用。
        </p>
      </header>

      {state === null ? (
        <p role="status" className="panel">
          正在准备教学客户端…
        </p>
      ) : (
        <>
          <section className="panel" aria-label="教学身份设置">
            <div className="grid items-center gap-4 sm:grid-cols-[minmax(0,20rem)_1fr]">
              <div>
                <label htmlFor="teaching-identity" className="mb-2 block text-sm font-semibold">
                  教学身份
                </label>
                <select
                  id="teaching-identity"
                  className="field"
                  value={state.identity.id}
                  onChange={(event) =>
                    controller.current?.setIdentity(event.currentTarget.value as IdentityId)
                  }
                >
                  {identities.map((identity) => (
                    <option key={identity.id} value={identity.id}>
                      {identity.label}
                    </option>
                  ))}
                </select>
              </div>
              <p className="text-sm leading-6 text-stone-600">
                切换身份会清空本页文件和原文。
                {state.identity.canWrite
                  ? '当前教学身份可提交上传；实际权限由服务器判断。'
                  : '当前教学身份只读，可以查看列表和下载自己的文档。'}
              </p>
            </div>
          </section>

          {state.ownerHasUnconfirmedUpload ? (
            <section className="panel border-amber-300 bg-amber-50" aria-label="未确认上传">
              <h2 className="text-lg font-semibold text-amber-950">仍有未确认上传</h2>
              <Notice code="upload_unconfirmed" />
              <p className="mt-2 text-sm leading-6 text-amber-900">
                刷新列表可核对当前文档；同名或相同 SHA 不能证明它来自哪次请求。
              </p>
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <Button
                  className="secondary-button"
                  disabled={!state.canAllowAnotherPost}
                  onClick={() => controller.current?.allowAnotherPost()}
                >
                  我已核对，允许再次上传（可能重复）
                </Button>
                {state.onePostPermission ? (
                  <p role="status" className="text-sm leading-6 text-amber-900">
                    已允许一次新上传，请重新选择文件。旧结果仍未确认。
                  </p>
                ) : null}
              </div>
            </section>
          ) : null}

          <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <div className="min-w-0 space-y-6">
              <section className="panel" aria-label="上传文件设置">
                <h2 className="mb-4 text-lg font-semibold">1. 选择并上传</h2>
                <label htmlFor="text-file" className="mb-2 block text-sm font-semibold">
                  选择文本文件
                </label>
                <input
                  key={state.fileInputResetKey}
                  id="text-file"
                  className="field file:mr-3 file:rounded-md file:border-0 file:bg-stone-100 file:px-3 file:py-1.5"
                  type="file"
                  accept=".txt,.md"
                  disabled={state.upload.phase === 'posting' || !state.identity.canWrite}
                  aria-describedby="upload-rules"
                  onChange={(event) =>
                    controller.current?.selectFile(event.currentTarget.files?.[0] ?? null)
                  }
                />
                <p id="upload-rules" className="mt-3 text-xs leading-6 text-stone-500">
                  .txt / .md，每份最多 4096 bytes，每人最多 3 份、合计 8192 bytes。
                  名称以字母或数字开头，主体只含英文字母、数字、下划线或连字符；同名再次上传会新建文档。
                </p>
                {state.selectedFile ? (
                  <div className="mt-4 rounded-lg bg-stone-50 p-3 text-sm" aria-label="已选文件">
                    <p className="plain-text font-medium">{state.selectedFile.name}</p>
                    <p className="mt-1 text-stone-600">
                      {state.selectedFile.size} bytes · {state.selectedFile.mediaType}
                    </p>
                  </div>
                ) : null}
                <Notice code={state.selectionNotice} />
                <div className="mt-5 flex flex-wrap gap-3">
                  <Button disabled={!state.canUpload} onClick={() => controller.current?.upload()}>
                    上传文件
                  </Button>
                  <Button
                    className="secondary-button"
                    disabled={!uploadBusy}
                    onClick={() => controller.current?.stopWaiting()}
                  >
                    停止等待
                  </Button>
                </div>
                <p className="mt-3 text-xs leading-6 text-stone-500">
                  停止只结束浏览器等待，不代表撤销服务器写入。
                </p>
              </section>

              <section
                className="panel"
                aria-label="上传结果"
                data-upload-phase={state.upload.phase}
              >
                <h2 className="text-lg font-semibold">2. 核对本次结果</h2>
                <p role="status" className="mt-3 text-sm leading-6">
                  {uploadProgress[state.upload.phase]}
                </p>
                <Notice
                  code={
                    state.ownerHasUnconfirmedUpload && state.upload.notice === 'upload_unconfirmed'
                      ? null
                      : state.upload.notice
                  }
                />
                {state.upload.phase === 'confirmed' && state.upload.metadata ? (
                  <div className="mt-4">
                    <Metadata document={state.upload.metadata} />
                  </div>
                ) : null}
              </section>
            </div>

            <section
              className="panel"
              aria-label="我的文档"
              aria-busy={state.listing.phase === 'loading'}
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-lg font-semibold">3. 我的文档</h2>
                <Button
                  className="secondary-button"
                  onClick={() => controller.current?.refreshList()}
                >
                  刷新我的列表
                </Button>
              </div>
              <p className="mt-3 text-sm leading-6 text-stone-600">
                当前列表观察到 {state.listing.documents.length} 份 · {observedBytes}{' '}
                bytes，实际配额由服务器裁定。
              </p>
              {state.listing.phase === 'loading' ? (
                <p role="status" className="mt-2 text-sm text-stone-500">
                  正在刷新，已有条目是上次观察结果。
                </p>
              ) : null}
              {state.listing.phase === 'error' ? (
                <p className="mt-2 text-sm text-stone-500">列表未刷新；已有条目是上次观察结果。</p>
              ) : null}
              <Notice code={state.listing.notice} />
              {state.listing.phase === 'ready' && state.listing.documents.length === 0 ? (
                <p role="status" className="mt-6 rounded-lg bg-stone-50 p-4 text-sm text-stone-600">
                  当前身份还没有文档。
                </p>
              ) : null}
              <div className="mt-5 space-y-4">
                {state.listing.documents.map((document) => (
                  <article
                    key={document.id}
                    aria-label={document.id}
                    data-document-id={document.id}
                    className="min-w-0 rounded-xl border border-stone-200 p-4"
                  >
                    <Metadata document={document} />
                    <div className="mt-4 flex flex-wrap gap-2">
                      <Button
                        className="secondary-button"
                        onClick={() => controller.current?.preview(document.id)}
                      >
                        查看纯文本
                      </Button>
                      <Button
                        className="secondary-button"
                        onClick={() => controller.current?.download(document.id)}
                      >
                        下载原始附件
                      </Button>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          </div>

          <section className="panel" aria-label="原文预览">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-semibold">原文与附件</h2>
              {state.content.action === 'preview' && state.content.phase !== 'idle' ? (
                <Button
                  className="secondary-button"
                  onClick={() => controller.current?.clearPreview()}
                >
                  关闭原文预览
                </Button>
              ) : null}
            </div>
            <p className="mt-2 text-xs leading-6 text-stone-500">
              读取后核对长度与 SHA。仅展示纯文本，不渲染 Markdown、执行 HTML 或访问其中链接。
            </p>
            {state.content.documentId ? (
              <p className="mt-3 font-mono text-sm text-stone-600">{state.content.documentId}</p>
            ) : null}
            {state.content.phase === 'idle' ? (
              <p className="mt-3 text-sm text-stone-600">
                从列表选择查看或下载，附件保留原始字节。
              </p>
            ) : null}
            {state.content.phase === 'loading' ? (
              <p role="status" className="mt-3 text-sm">
                正在读取并校验原始字节…
              </p>
            ) : null}
            {state.content.phase === 'downloaded' ? (
              <p role="status" className="mt-3 text-sm">
                已校验原字节并发起附件下载。
              </p>
            ) : null}
            <Notice code={state.content.notice} />
            {state.content.phase === 'preview' && state.content.text !== null ? (
              <pre
                aria-label="纯文本原文"
                className="plain-text mt-4 rounded-lg bg-stone-50 p-4 font-mono text-sm leading-7"
              >
                {state.content.text}
              </pre>
            ) : null}
          </section>
        </>
      )}

      <footer className="max-w-4xl text-xs leading-6 text-stone-500">
        存储方式由启动命令决定：memory 重启清空，显式 SQLite 保留原文与配额。
        页面不持久保存文件或身份反馈，刷新会丢失本页提示。将实际现象记入包内 EVIDENCE.md
        和课程证据卡；本实验不包含生产登录、解析器或文档问答。
      </footer>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
