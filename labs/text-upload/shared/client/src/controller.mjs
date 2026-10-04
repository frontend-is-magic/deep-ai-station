import fixtures from './fixtures.json' with { type: 'json' };
import {
  ProtocolError,
  attachmentName,
  contentHeadersMatch,
  decodePreview,
  identities,
  inspectFile,
  parseApiError,
  parseListing,
  parseMetadata,
  readBoundedBody,
  readProtocolJson,
} from './protocol.mjs';

const tokens = Object.freeze({
  alice: 'lab-alice-session',
  'alice-second': 'lab-alice-second-session',
  'alice-readonly': 'lab-alice-readonly-session',
  bob: 'lab-bob-session',
  expired: 'lab-expired-session',
  revoked: 'lab-revoked-session',
});
const sessionFor = (identity) =>
  fixtures.sessions.find(
    (session) =>
      session.token === tokens[identity.id] &&
      session.user_id === identity.owner &&
      session.can_write === identity.canWrite,
  );
const emptyUpload = () => ({ phase: 'idle', attemptId: null, metadata: null, notice: null });
const emptyListing = () => ({ phase: 'idle', documents: [], notice: null });
const emptyContent = () => ({
  phase: 'idle',
  action: null,
  documentId: null,
  text: null,
  notice: null,
});
const freeze = (value) => {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
};
const defaults = {
  fetch: (url, init) => globalThis.fetch(url, init),
  readFile: (file) => file.arrayBuffer(),
  sha256: async (bytes) => {
    const hash = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    return [...new Uint8Array(hash)].map((value) => value.toString(16).padStart(2, '0')).join('');
  },
  download: (bytes, filename) => {
    const url = URL.createObjectURL(new Blob([bytes], { type: 'application/octet-stream' }));
    try {
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
    } finally {
      URL.revokeObjectURL(url);
    }
  },
  setTimer: (callback, milliseconds) => setTimeout(callback, milliseconds),
  clearTimer: (handle) => clearTimeout(handle),
};

export function createUploadController(options = {}) {
  const deps = { ...defaults, ...options.dependencies };
  const deadlineMs = options.deadlineMs ?? 10000;
  if (!Number.isFinite(deadlineMs) || deadlineMs < 1 || deadlineMs > 10000) {
    throw new Error('invalid_deadline');
  }
  const initialIdentity = identities.find(
    (identity) => identity.id === (options.initialIdentity ?? 'alice'),
  );
  if (!initialIdentity || identities.some((identity) => !sessionFor(identity))) {
    throw new Error('invalid_teaching_fixtures');
  }
  let started = false;
  let disposed = false;
  let transitioning = false;
  let selectedFile = null;
  let selectionVersion = 0;
  let permission = false;
  let attemptSequence = 0;
  const unconfirmedOwners = new Set();
  const listeners = new Set();
  const slots = { upload: null, list: null, content: null };
  let state = {
    identity: initialIdentity,
    generation: 0,
    fileInputResetKey: 0,
    selectedFile: null,
    selectionNotice: null,
    upload: emptyUpload(),
    listing: emptyListing(),
    content: emptyContent(),
  };
  const derive = (value) => {
    const unknown = unconfirmedOwners.has(value.identity.owner);
    const busy = ['reading', 'hashing', 'posting'].includes(value.upload.phase);
    return freeze({
      ...value,
      ownerHasUnconfirmedUpload: unknown,
      onePostPermission: permission,
      canUpload:
        !disposed &&
        !transitioning &&
        value.identity.canWrite &&
        !!value.selectedFile &&
        !busy &&
        (!unknown || permission),
      canAllowAnotherPost:
        !disposed && !transitioning && value.identity.canWrite && unknown && !busy && !permission,
    });
  };
  state = derive(state);
  const publish = (patch) => {
    if (disposed) return;
    state = derive({ ...state, ...patch });
    for (const listener of [...listeners]) {
      try {
        listener();
      } catch {
        // A view subscriber cannot alter request outcome or leak arbitrary diagnostics.
      }
    }
  };
  const current = (ticket) =>
    !disposed &&
    slots[ticket.channel] === ticket &&
    state.generation === ticket.generation &&
    state.identity.id === ticket.identity.id &&
    (ticket.channel !== 'upload' || ticket.selectionVersion === selectionVersion);
  const abortTicket = (ticket) => {
    const prior = transitioning;
    transitioning = true;
    try {
      ticket.controller.abort();
    } finally {
      transitioning = prior;
    }
  };
  const detachAll = () => {
    const tickets = Object.values(slots).filter(Boolean);
    for (const channel of Object.keys(slots)) slots[channel] = null;
    for (const ticket of tickets) deps.clearTimer(ticket.timer);
    return tickets;
  };
  const retire = (ticket, abort = false) => {
    if (slots[ticket.channel] === ticket) slots[ticket.channel] = null;
    deps.clearTimer(ticket.timer);
    if (abort) abortTicket(ticket);
  };
  const clearFile = () => {
    selectedFile = null;
    selectionVersion++;
    return {
      selectedFile: null,
      selectionNotice: null,
      fileInputResetKey: state.fileInputResetKey + 1,
    };
  };
  const finishUpload = (ticket, phase, notice = null, metadata = null) => {
    if (!current(ticket)) return;
    if (phase === 'unconfirmed') unconfirmedOwners.add(ticket.identity.owner);
    retire(ticket);
    const filePatch = ticket.dispatched ? clearFile() : {};
    publish({ ...filePatch, upload: { phase, attemptId: ticket.attemptId, metadata, notice } });
  };
  const stop = (ticket, timeout) => {
    if (!current(ticket)) return;
    if (ticket.channel === 'upload') {
      if (ticket.dispatched) {
        finishUpload(ticket, 'unconfirmed', 'upload_unconfirmed');
      } else {
        finishUpload(ticket, 'stopped', timeout ? 'not_sent_timeout' : 'not_sent_stopped');
      }
    } else {
      retire(ticket);
      if (ticket.channel === 'list') {
        publish({ listing: { ...state.listing, phase: 'error', notice: 'request_timeout' } });
      } else {
        publish({
          content: { ...state.content, phase: 'error', text: null, notice: 'request_timeout' },
        });
      }
    }
    // Identity is invalidated before abort can synchronously invoke hostile callbacks.
    abortTicket(ticket);
  };
  const begin = (channel, extra = {}) => {
    if (slots[channel]) retire(slots[channel], true);
    const ticket = {
      channel,
      identity: state.identity,
      generation: state.generation,
      controller: new AbortController(),
      selectionVersion,
      dispatched: false,
      ...extra,
    };
    slots[channel] = ticket;
    ticket.timer = deps.setTimer(() => stop(ticket, true), deadlineMs);
    return ticket;
  };
  const request = (ticket, method, extra = {}) => ({
    method,
    credentials: 'omit',
    cache: 'no-store',
    redirect: 'error',
    signal: ticket.controller.signal,
    ...extra,
    headers: { Authorization: `Bearer ${sessionFor(ticket.identity).token}`, ...extra.headers },
  });
  const responseError = (response, value) =>
    parseApiError(response.status, value)?.code ?? 'invalid_response';
  const readNotice = (error) => (error instanceof ProtocolError ? error.code : 'network_failed');
  const discardResponse = (response) => {
    try {
      Promise.resolve(response.body?.cancel()).catch(() => {});
    } catch {
      /* already closed */
    }
  };

  function refreshList() {
    if (disposed || transitioning || !started) return;
    const ticket = begin('list');
    publish({ listing: { ...state.listing, phase: 'loading', notice: null } });
    void (async () => {
      try {
        if (!current(ticket)) return;
        const response = await deps.fetch('/api/documents', request(ticket, 'GET'));
        if (!current(ticket)) {
          discardResponse(response);
          return;
        }
        const value = await readProtocolJson(response, ticket.controller.signal);
        if (!current(ticket)) return;
        const documents = response.status === 200 ? parseListing(value) : null;
        retire(ticket);
        publish({
          listing: documents
            ? { phase: 'ready', documents, notice: null }
            : { ...state.listing, phase: 'error', notice: responseError(response, value) },
        });
      } catch (error) {
        if (!current(ticket)) return;
        retire(ticket);
        publish({ listing: { ...state.listing, phase: 'error', notice: readNotice(error) } });
      } finally {
        if (slots.list === ticket) retire(ticket);
      }
    })();
  }
  function content(documentId, action) {
    if (disposed || transitioning || !started) return;
    const metadata = state.listing.documents.find((document) => document.id === documentId);
    if (!metadata) return;
    const ticket = begin('content');
    publish({ content: { phase: 'loading', action, documentId, text: null, notice: null } });
    void (async () => {
      try {
        if (!current(ticket)) return;
        const response = await deps.fetch(
          `/api/documents/${metadata.id}/content`,
          request(ticket, 'GET'),
        );
        if (!current(ticket)) {
          discardResponse(response);
          return;
        }
        if (response.status !== 200) {
          const value = await readProtocolJson(response, ticket.controller.signal);
          if (!current(ticket)) return;
          throw new ProtocolError(responseError(response, value));
        }
        if (!contentHeadersMatch(response, metadata)) {
          try {
            Promise.resolve(response.body?.cancel()).catch(() => {});
          } catch {
            /* already closed */
          }
          throw new ProtocolError('content_integrity_failed');
        }
        const bytes = await readBoundedBody(response, 4096, ticket.controller.signal);
        if (!current(ticket)) return;
        if (bytes.byteLength !== metadata.size_bytes)
          throw new ProtocolError('content_integrity_failed');
        const hash = await deps.sha256(bytes, ticket.controller.signal);
        if (!current(ticket)) return;
        if (hash !== metadata.sha256) throw new ProtocolError('content_integrity_failed');
        let text = null;
        if (action === 'preview') {
          text = decodePreview(bytes);
          if (text === null) throw new ProtocolError('content_not_utf8');
        } else {
          try {
            deps.download(bytes.slice(), attachmentName(metadata));
          } catch {
            throw new ProtocolError('download_failed');
          }
          if (!current(ticket)) return;
        }
        retire(ticket);
        publish({
          content: {
            phase: action === 'preview' ? 'preview' : 'downloaded',
            action,
            documentId,
            text,
            notice: null,
          },
        });
      } catch (error) {
        if (!current(ticket)) return;
        retire(ticket);
        publish({
          content: { phase: 'error', action, documentId, text: null, notice: readNotice(error) },
        });
      } finally {
        if (slots.content === ticket) retire(ticket);
      }
    })();
  }
  return Object.freeze({
    getSnapshot: () => state,
    subscribe(listener) {
      if (disposed) return () => {};
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    start() {
      if (disposed || transitioning || started) return;
      started = true;
      refreshList();
    },
    setIdentity(id) {
      if (disposed || transitioning || state.identity.id === id) return;
      const identity = identities.find((item) => item.id === id);
      if (!identity) return;
      if (slots.upload?.dispatched) unconfirmedOwners.add(slots.upload.identity.owner);
      transitioning = true;
      const tickets = detachAll();
      permission = false;
      state = derive({
        ...state,
        ...clearFile(),
        identity,
        generation: state.generation + 1,
        upload: emptyUpload(),
        listing: emptyListing(),
        content: emptyContent(),
      });
      const generation = state.generation;
      try {
        for (const ticket of tickets) ticket.controller.abort();
      } finally {
        transitioning = false;
      }
      publish({});
      if (!disposed && state.generation === generation) refreshList();
    },
    selectFile(file) {
      if (disposed || transitioning || slots.upload?.dispatched) return;
      if (slots.upload) {
        retire(slots.upload, true);
        publish({ upload: emptyUpload() });
      }
      selectionVersion++;
      selectedFile = null;
      if (file === null) {
        publish(clearFile());
        return;
      }
      const inspected = inspectFile(file);
      if (!inspected.ok) {
        publish({ ...clearFile(), selectionNotice: inspected.notice });
        return;
      }
      selectedFile = file;
      publish({ selectedFile: inspected.file, selectionNotice: null });
    },
    upload() {
      if (disposed || transitioning || !started || !state.canUpload || !selectedFile) return;
      const file = selectedFile;
      const selected = state.selectedFile;
      const ticket = begin('upload', { attemptId: ++attemptSequence });
      publish({
        upload: { phase: 'reading', attemptId: ticket.attemptId, metadata: null, notice: null },
      });
      void (async () => {
        let stage = 'reading';
        try {
          if (!current(ticket)) return;
          const buffer = await deps.readFile(file, ticket.controller.signal);
          if (!current(ticket)) return;
          if (
            !(buffer instanceof ArrayBuffer) ||
            buffer.byteLength !== selected.size ||
            buffer.byteLength > 4096
          ) {
            finishUpload(ticket, 'local_error', 'file_size_changed');
            return;
          }
          const bytes = new Uint8Array(buffer).slice();
          stage = 'hashing';
          publish({ upload: { ...state.upload, phase: 'hashing' } });
          if (!current(ticket)) return;
          const hash = await deps.sha256(bytes, ticket.controller.signal);
          if (!current(ticket)) return;
          if (typeof hash !== 'string' || !/^[0-9a-f]{64}$/.test(hash) || hash.length !== 64) {
            finishUpload(ticket, 'local_error', 'digest_failed');
            return;
          }
          publish({ upload: { ...state.upload, phase: 'posting' } });
          if (!current(ticket)) return;
          ticket.dispatched = true;
          permission = false;
          const pendingResponse = deps.fetch(
            '/api/documents',
            request(ticket, 'POST', {
              headers: { 'Content-Type': selected.mediaType, 'X-Filename': selected.name },
              body: bytes,
            }),
          );
          if (current(ticket)) publish({});
          const response = await pendingResponse;
          if (!current(ticket)) {
            discardResponse(response);
            return;
          }
          const value = await readProtocolJson(response, ticket.controller.signal);
          if (!current(ticket)) return;
          const metadata = response.status === 201 ? parseMetadata(value) : null;
          if (
            metadata &&
            metadata.filename === selected.name &&
            metadata.media_type === selected.mediaType &&
            metadata.size_bytes === bytes.length &&
            metadata.sha256 === hash
          ) {
            finishUpload(ticket, 'confirmed', null, metadata);
            if (
              !disposed &&
              state.generation === ticket.generation &&
              state.identity.id === ticket.identity.id
            )
              refreshList();
            return;
          }
          const rejected = parseApiError(response.status, value);
          if (
            rejected &&
            (response.status < 500 ||
              ['session_store_unavailable', 'repository_unavailable'].includes(rejected.code))
          ) {
            finishUpload(ticket, 'rejected', rejected.code);
          } else finishUpload(ticket, 'unconfirmed', 'upload_unconfirmed');
        } catch {
          if (!current(ticket)) return;
          if (ticket.dispatched) finishUpload(ticket, 'unconfirmed', 'upload_unconfirmed');
          else
            finishUpload(
              ticket,
              'local_error',
              stage === 'reading' ? 'file_read_failed' : 'digest_failed',
            );
        } finally {
          if (slots.upload === ticket) retire(ticket);
        }
      })();
    },
    stopWaiting() {
      if (!disposed && !transitioning && slots.upload) stop(slots.upload, false);
    },
    refreshList,
    preview: (id) => content(id, 'preview'),
    download: (id) => content(id, 'download'),
    clearPreview() {
      if (disposed || transitioning) return;
      if (slots.content) retire(slots.content, true);
      publish({ content: emptyContent() });
    },
    allowAnotherPost() {
      if (disposed || transitioning || !state.canAllowAnotherPost) return;
      permission = true;
      publish(clearFile());
    },
    dispose() {
      if (disposed) return;
      if (slots.upload?.dispatched) unconfirmedOwners.add(slots.upload.identity.owner);
      disposed = true;
      listeners.clear();
      const tickets = detachAll();
      permission = false;
      state = derive({
        ...state,
        ...clearFile(),
        generation: state.generation + 1,
        upload: emptyUpload(),
        listing: emptyListing(),
        content: emptyContent(),
      });
      for (const ticket of tickets) abortTicket(ticket);
    },
  });
}
