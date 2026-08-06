// rag.ts — RAG health, documents, settings
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;

AppRegistry.register('rag', {
  state: {
    ragHealth: {}, ragHealthData: null, ragDocs: [], ragDocsCount: 0,
    ragImport: { title: '', discipline_id: '' }, ragUploadFile: null,
    ragImporting: false, ragImportResult: null,
    ragSettings: {
      embedding_provider: '', embedding_model: '', embedding_api_key: '', embedding_api_base: '',
      embedding_dimensions: 1536, chunker_type: 'recursive', chunk_size: 768, chunk_overlap: 160,
      reranker_enabled: false, reranker_k1: 1.5, reranker_b: 0.75,
      cache_enabled: false, cache_ttl: 300, cache_maxsize: 256,
    },
    ragStats: null, ragSettingsLoading: false, ragSettingsSaving: false,
    ragSettingsSaveMsg: '', ragStatsLoading: false, ragTab: 'docs',
  },

  methods() {
    const api = () => w.Alpine.store('api');
    const notify = () => w.Alpine.store('notify');

    return {
      async refreshRag(this: any) {
        try { this.ragHealth = await api().get('/api/rag/health'); }
        catch (e: unknown) { this.ragHealth = { status: 'error', error: e instanceof Error ? e.message : String(e) }; }
        try {
          const docsResp = await api().post('/api/rag/documents/list', { limit: 100 });
          this.ragDocs = docsResp.documents || [];
          this.ragDocsCount = docsResp.count ?? this.ragDocs.length;
        } catch { this.ragDocs = []; this.ragDocsCount = 0; }
      },

      async loadRagSettings(this: any) {
        this.ragSettingsLoading = true; this.ragSettingsSaveMsg = '';
        try { this.ragSettings = await api().get('/api/rag/config'); }
        catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
        finally { this.ragSettingsLoading = false; }
      },

      async loadRagStats(this: any) {
        this.ragStatsLoading = true;
        try { this.ragStats = await api().get('/api/rag/stats'); }
        catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
        finally { this.ragStatsLoading = false; }
      },

      async saveRagSettings(this: any) {
        this.ragSettingsSaving = true; this.ragSettingsSaveMsg = '';
        try {
          await api().put('/api/rag/config', this.ragSettings);
          this.ragSettingsSaveMsg = 'saved'; notify().success('RAG settings saved');
          setTimeout(() => { this.ragSettingsSaveMsg = ''; }, 3000);
        } catch (e: unknown) { this.ragSettingsSaveMsg = 'error'; notify().error(e instanceof Error ? e.message : String(e)); }
        finally { this.ragSettingsSaving = false; }
      },

      ragDropFile(this: any, event: DragEvent) {
        const file = event.dataTransfer?.files?.[0];
        if (file) this.ragUploadFile = file;
      },

      async uploadRagDoc(this: any) {
        if (!this.ragUploadFile) return;
        this.ragImporting = true; this.ragImportResult = null;
        try {
          const fd = new FormData();
          fd.append('file', this.ragUploadFile);
          if (this.ragImport.title) fd.append('title', this.ragImport.title);
          if (this.ragImport.discipline_id) fd.append('discipline_id', this.ragImport.discipline_id);
          const token = localStorage.getItem('admin_token');
          const headers: Record<string, string> = {};
          if (token) headers['Authorization'] = 'Bearer ' + token;
          const res = await fetch('/api/rag/documents/upload', { method: 'POST', headers, body: fd });
          const result = await res.json();
          if (!res.ok) {
            const err = result.message || result.error || res.statusText;
            this.ragImportResult = { error: err }; notify().error(String(err));
          } else {
            this.ragImportResult = result; this.ragUploadFile = null;
            this.ragImport = { title: '', discipline_id: '' }; notify().success('Document uploaded');
            await this.refreshRag();
          }
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : String(e);
          this.ragImportResult = { error: msg }; notify().error(msg);
        } finally { this.ragImporting = false; }
      },

      async deleteRagDoc(this: any, doc: any) {
        const docId = doc.id || doc.document_id;
        const docPath = doc.source_path || doc.path;
        const docName = doc.title || docId;
        if (!confirm('Delete document "' + docName + '"?')) return;
        try {
          const body = docId ? { document_id: docId } : { path: docPath };
          await api().post('/api/rag/documents/delete', body);
          notify().success('Document deleted');
          await this.refreshRag();
        } catch (e: unknown) { notify().error(e instanceof Error ? e.message : String(e)); }
      },
    };
  },
});

export {};
