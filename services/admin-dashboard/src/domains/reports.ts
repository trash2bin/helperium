// reports.ts — Widget problem reports (review surface for /api/reports on api-service)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;


AppRegistry.register('reports', {
  state: {
    reportsList: [], reportsTotal: 0, reportsStatus: '', reportsLoading: false,
    reportsError: '', reportsExpanded: '',
  },

  methods() {
    const api = () => w.Alpine.store('api');
    const notify = () => w.Alpine.store('notify');

    return {
      async loadReports(this: any) {
        this.reportsLoading = true; this.reportsError = '';
        try {
          let url = '/api/reports?limit=100';
          if (this.reportsStatus) url += '&status=' + encodeURIComponent(this.reportsStatus);
          const resp = await api().get(url);
          this.reportsList = resp.reports || [];
          this.reportsTotal = resp.total ?? this.reportsList.length;
        } catch (e: unknown) {
          this.reportsError = (e instanceof Error ? e.message : 'Failed to load reports') || '';
          notify().error(this.reportsError);
        } finally { this.reportsLoading = false; }
      },

      async markReportReviewed(this: any, rep: any) {
        try {
          await api().post('/api/reports/' + encodeURIComponent(rep.id) + '/status', { status: 'reviewed' });
          rep.status = 'reviewed';
          notify().success(__('reports.markedDone'));
        } catch (e: unknown) {
          const msg = (e instanceof Error ? e.message : 'Failed to update report') || '';
          notify().error(msg);
        }
      },

      reportsTimestamp(this: any, epochSeconds: any) {
        if (epochSeconds === null || epochSeconds === undefined) return '';
        try {
          return new Date(Number(epochSeconds) * 1000).toLocaleString();
        } catch { return String(epochSeconds); }
      },

      reportsExcerpt(this: any, rep: any) {
        return rep.message_text || rep.last_error_text || '—';
      },

      async copyText(this: any, text: string) {
        try {
          await navigator.clipboard.writeText(text);
          notify().success(__('reports.copied'));
        } catch {
          notify().error(__('reports.copyFailed'));
        }
      },
    };
  },
});

export {};
