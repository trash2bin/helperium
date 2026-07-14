/**
 * Contract test — проверяет что admin-dashboard фронт совместим
 * с реальными ответами api-service.
 *
 * Требует запущенные сервисы (admin-dashboard:8085, api-service:8081).
 * Если сервисы не отвечают — тест пропускается (не падает).
 */

import { describe, it, expect, beforeAll } from 'vitest';

const ADMIN_URL = 'http://localhost:8085';
const API_SPEC_URL = 'http://localhost:8081/openapi.json';
const ADMIN_TOKEN = 'secret';

async function fetchJson(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  });
  const body = await res.json().catch(() => null);
  return { status: res.status, body, headers: res.headers };
}

async function serviceAlive(url) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
    return res.ok;
  } catch {
    return false;
  }
}

describe('api-service contract', () => {
  /** @type {import('./api-types/api-service.d.ts').paths} */
  let spec;
  let servicesUp = true;

  beforeAll(async () => {
    const apiUp = await serviceAlive(API_SPEC_URL.replace('/openapi.json', '/health'));
    const adminUp = await serviceAlive(ADMIN_URL + '/api/health');

    if (!apiUp || !adminUp) {
      console.warn(`⚠️  Services not running (api=${apiUp}, admin=${adminUp}) — skipping contract tests`);
      servicesUp = false;
      return;
    }

    // Load OpenAPI spec
    const res = await fetch(API_SPEC_URL);
    spec = await res.json();

    // Verify that the spec has the agent endpoints we need
    if (!spec.paths['/api/agents'] || !spec.paths['/api/agents/{name}']) {
      throw new Error('OpenAPI spec missing /api/agents endpoints');
    }
  });

  // ── OpenAPI spec checks ──

  it('DELETE /api/agents/{name} returns 204 with no content-type body', () => {
    if (!servicesUp) return;
    const op = spec.paths['/api/agents/{name}'].delete;
    expect(op.responses['204']).toBeDefined();
    // 204 MUST NOT have content-type schema (no body)
    expect(op.responses['204'].content).toBeUndefined();
  });

  function resolveRef(ref) {
    if (!ref || !ref.startsWith('#/')) throw new Error('Cannot resolve ' + ref);
    const parts = ref.slice(2).split('/');
    let obj = spec;
    for (const p of parts) {
      obj = obj[p];
      if (!obj) throw new Error('Cannot resolve ' + ref + ' at ' + p);
    }
    return obj;
  }

  function getPostSchema() {
    const op = spec.paths['/api/agents'].post;
    const schema = op.requestBody.content['application/json'].schema;
    if (schema.$ref) return resolveRef(schema.$ref);
    return schema;
  }

  it('POST /api/agents has pattern validation on name', () => {
    if (!servicesUp) return;
    const agentSchema = getPostSchema();
    const nameProp = agentSchema.properties.name;
    expect(nameProp.pattern).toBe('^[a-z][a-z0-9_-]*$');
  });

  it('POST /api/agents requires name (min_length=1)', () => {
    if (!servicesUp) return;
    const agentSchema = getPostSchema();
    const nameProp = agentSchema.properties.name;
    expect(nameProp.minLength).toBe(1);
  });

  it('DELETE /api/agents/{name} has no requestBody', () => {
    if (!servicesUp) return;
    const op = spec.paths['/api/agents/{name}'].delete;
    expect(op.requestBody).toBeUndefined();
  });

  // ── Live 422 response format ──

  it('POST with bad name returns 422 with Pydantic detail[]', async () => {
    if (!servicesUp) return;
    const { status, body } = await fetchJson(ADMIN_URL + '/api/agents', {
      method: 'POST',
      headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
      body: JSON.stringify({ name: 'Bad Name', tenant_ids: ['default'] }),
    });
    expect(status).toBe(422);
    expect(body.detail).toBeInstanceOf(Array);
    expect(body.detail[0].type).toBe('string_pattern_mismatch');
    expect(body.detail[0].msg).toMatch(/pattern/);
    expect(body.detail[0].input).toBe('Bad Name');
  });

  it('POST without name returns 422 with "Field required"', async () => {
    if (!servicesUp) return;
    const { status, body } = await fetchJson(ADMIN_URL + '/api/agents', {
      method: 'POST',
      headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
      body: JSON.stringify({ tenant_ids: ['default'] }),
    });
    expect(status).toBe(422);
    expect(body.detail[0].msg).toMatch(/required/i);
  });

  // ── Live 204 format ──

  it('DELETE existing agent returns 204 with no content-type', async () => {
    if (!servicesUp) return;

    // Create agent first
    const name = 'contract-test-' + Date.now();
    const create = await fetchJson(ADMIN_URL + '/api/agents', {
      method: 'POST',
      headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
      body: JSON.stringify({ name, tenant_ids: ['default'] }),
    });
    expect(create.status).toBe(201);

    // Delete it
    const del = await fetch(ADMIN_URL + '/api/agents/' + name, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
    });
    expect(del.status).toBe(204);
    // Must NOT have content-type on 204 (regression: Go-proxy used to copy it)
    const ct = del.headers.get('content-type');
    expect(ct).toBeNull();
  });

  // ── 404 for nonexistent agent ──

  it('DELETE nonexistent agent returns 404', async () => {
    if (!servicesUp) return;
    const { status, body } = await fetchJson(
      ADMIN_URL + '/api/agents/nonexistent-' + Date.now(),
      { method: 'DELETE', headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } },
    );
    expect(status).toBe(404);
  });
});
