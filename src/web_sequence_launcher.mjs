import { mkdtempSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import net from 'node:net';
import { chromium } from 'playwright-core';

const INPUT = await new Promise((resolve, reject) => {
  let data = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => {
    data += chunk;
  });
  process.stdin.on('end', () => resolve(data));
  process.stdin.on('error', reject);
});

const payload = JSON.parse(INPUT || '{}');

function chromePath() {
  const candidates = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ];
  return candidates.find((p) => existsSync(p));
}

async function freePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = address && typeof address === 'object' ? address.port : null;
      server.close((error) => {
        if (error) reject(error);
        else resolve(port);
      });
    });
    server.on('error', reject);
  });
}

async function waitForDebugger(port, timeoutMs = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) return true;
    } catch {
      // ignore
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`Chrome debugger did not start on port ${port}`);
}

async function setFieldValue(page, selector, value) {
  const locator = page.locator(selector).first();
  await locator.waitFor({ state: 'visible', timeout: 20000 });
  try {
    await locator.fill(value, { timeout: 10000 });
  } catch {
    await locator.evaluate((el, val) => {
      el.value = val;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.focus();
    }, value);
  }
}

async function dismissCommonBanners(page) {
  const labels = ['Accept all', 'Accept', 'I agree', 'Agree', 'Continue'];
  for (const label of labels) {
    try {
      const button = page.getByRole('button', { name: new RegExp(`^${label}$`, 'i') }).first();
      if (await button.isVisible({ timeout: 700 })) await button.click({ timeout: 1000 });
    } catch {
      // best effort only
    }
  }
}

function sequenceFieldSelectors(extra = []) {
  return [
    ...extra,
    'textarea[name*="sequence" i]',
    'textarea[id*="sequence" i]',
    'textarea[placeholder*="sequence" i]',
    'textarea[placeholder*="protein" i]',
    'textarea[placeholder*="fasta" i]',
    'textarea[aria-label*="sequence" i]',
    'textarea[aria-label*="protein" i]',
    'textarea[aria-label*="fasta" i]',
    'input[name*="sequence" i]',
    'input[id*="sequence" i]',
    'input[placeholder*="sequence" i]',
    'input[placeholder*="protein" i]',
    'input[placeholder*="fasta" i]',
    'input[aria-label*="sequence" i]',
    'input[aria-label*="protein" i]',
    'input[aria-label*="fasta" i]',
    'textarea',
    '[contenteditable="true"]',
    'input[type="text"]',
    'input:not([type])',
  ];
}

async function fillLikelySequenceField(page, value, selectors = []) {
  const joined = sequenceFieldSelectors(selectors).join(',');

  const deadline = Date.now() + 25000;
  let lastError = null;
  while (Date.now() < deadline) {
    const handles = await page.locator(joined).elementHandles();
    const ranked = await Promise.all(
      handles.map(async (handle) => {
        try {
          const score = await handle.evaluate((el) => {
            const rect = el.getBoundingClientRect();
            if (!rect.width || !rect.height) return -1;
            const text = [
              el.getAttribute('aria-label') || '',
              el.getAttribute('placeholder') || '',
              el.getAttribute('name') || '',
              el.getAttribute('id') || '',
              el.closest('label')?.textContent || '',
              el.closest('form')?.textContent || '',
              el.parentElement?.textContent || '',
            ]
              .join(' ')
              .toLowerCase();
            let scoreValue = el.tagName === 'TEXTAREA' ? 20 : 0;
            if (text.includes('blast')) scoreValue += 14;
            if (text.includes('protein')) scoreValue += 12;
            if (text.includes('sequence')) scoreValue += 10;
            if (text.includes('fasta')) scoreValue += 10;
            if (text.includes('query')) scoreValue += 5;
            if (text.includes('search') && el.tagName !== 'TEXTAREA') scoreValue -= 8;
            if (el.disabled || el.readOnly) scoreValue -= 20;
            return scoreValue;
          });
          return { handle, score };
        } catch (error) {
          lastError = error;
          return { handle, score: -1 };
        }
      })
    );
    ranked.sort((a, b) => b.score - a.score);
    for (const { handle, score } of ranked) {
      if (score < 0) continue;
      try {
        const filled = await handle.evaluate((el, val) => {
          const rect = el.getBoundingClientRect();
          if (!rect.width || !rect.height) return false;
          el.scrollIntoView({ block: 'center', inline: 'center' });
          el.focus();
          if (el.isContentEditable) {
            el.textContent = val;
          } else {
            const proto =
              el.tagName === 'TEXTAREA'
                ? window.HTMLTextAreaElement.prototype
                : window.HTMLInputElement.prototype;
            const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
            if (el._valueTracker) el._valueTracker.setValue('');
            if (descriptor && descriptor.set) descriptor.set.call(el, val);
            else el.value = val;
          }
          el.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, inputType: 'insertText', data: val }));
          el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: val }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'A' }));
          el.dispatchEvent(new Event('blur', { bubbles: true }));
          el.focus();
          const current = el.isContentEditable ? el.textContent : el.value;
          return current && current.length >= Math.min(20, val.length);
        }, value);
        if (filled) return true;
      } catch (error) {
        lastError = error;
      }
      try {
        await handle.click({ timeout: 2000 });
        const modifier = process.platform === 'darwin' ? 'Meta' : 'Control';
        await page.keyboard.press(`${modifier}+A`);
        await page.keyboard.insertText(value);
        const filled = await handle.evaluate((el, val) => {
          const current = el.isContentEditable ? el.textContent : el.value;
          return current && current.length >= Math.min(20, val.length);
        }, value);
        if (filled) return true;
      } catch (error) {
        lastError = error;
      }
    }
    await page.waitForTimeout(500);
  }
  throw lastError || new Error('No visible sequence input field accepted the sequence.');
}

async function fillLikelyTextField(page, value, selectors = []) {
  const joined = [
    ...selectors,
    'textarea[placeholder*="motif" i]',
    'textarea[placeholder*="residue" i]',
    'textarea[placeholder*="sequence" i]',
    'textarea[placeholder*="query" i]',
    'input[placeholder*="motif" i]',
    'input[placeholder*="residue" i]',
    'input[placeholder*="sequence" i]',
    'input[placeholder*="query" i]',
    'textarea',
    'input[type="text"]',
    'input:not([type])',
    '[contenteditable="true"]',
  ].join(',');
  const deadline = Date.now() + 25000;
  let lastError = null;
  while (Date.now() < deadline) {
    const handles = await page.locator(joined).elementHandles();
    for (const handle of handles) {
      try {
        const filled = await handle.evaluate((el, val) => {
          const rect = el.getBoundingClientRect();
          if (!rect.width || !rect.height) return false;
          el.scrollIntoView({ block: 'center', inline: 'center' });
          if (el.isContentEditable) {
            el.textContent = val;
          } else {
            const proto =
              el.tagName === 'TEXTAREA'
                ? window.HTMLTextAreaElement.prototype
                : window.HTMLInputElement.prototype;
            const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
            if (descriptor && descriptor.set) descriptor.set.call(el, val);
            else el.value = val;
          }
          el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: val }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          el.focus();
          const current = el.isContentEditable ? el.textContent : el.value;
          return current && current.length >= Math.min(3, val.length);
        }, value);
        if (filled) return true;
      } catch (error) {
        lastError = error;
      }
    }
    await page.waitForTimeout(500);
  }
  throw lastError || new Error('No visible text field accepted the value.');
}

async function uploadLikelyFiles(page, files = []) {
  if (!files.length) return false;
  await page.waitForTimeout(2500);
  const inputs = page.locator('input[type="file"]');
  const count = await inputs.count();
  if (!count) throw new Error('No file upload input found.');
  try {
    await inputs.first().setInputFiles(files);
    return true;
  } catch (error) {
    if (count < files.length) throw error;
    for (let i = 0; i < files.length; i += 1) {
      await inputs.nth(i).setInputFiles(files[i]);
    }
    return true;
  }
}

async function fillUniProt(page, fasta) {
  await page.goto('https://www.uniprot.org/blast', { waitUntil: 'domcontentloaded' });
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await dismissCommonBanners(page);
  await fillLikelySequenceField(page, fasta, [
    'textarea[name*="sequence" i]',
    'textarea[id*="sequence" i]',
    'textarea[placeholder*="FASTA" i]',
    'textarea[placeholder*="protein" i]',
    'textarea[aria-label*="sequence" i]',
  ]);
}

async function fillNCBI(page, fasta) {
  const url =
    'https://blast.ncbi.nlm.nih.gov/Blast.cgi?PAGE_TYPE=BlastSearch&PROGRAM=blastp&QUERY=' +
    encodeURIComponent(fasta);
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  const area = page.locator('textarea[name="QUERY"], textarea#seq, textarea').first();
  await area.waitFor({ state: 'visible', timeout: 20000 });
}

async function fillHmmer(page, fasta) {
  await page.goto('https://www.ebi.ac.uk/Tools/hmmer/search/phmmer', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  await fillLikelySequenceField(page, fasta, ['textarea[name="input"]']);
}

async function fillInterPro(page, fasta) {
  await page.goto('https://www.ebi.ac.uk/interpro/search/sequence/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  await fillLikelySequenceField(page, fasta, ['#search-terms-autocomplete']);
}

async function fillRcsb(page, fasta) {
  const sequence = fasta
    .split(/\r?\n/)
    .filter((line) => line.trim() && !line.startsWith('>'))
    .join('');
  await page.goto('https://www.rcsb.org', { waitUntil: 'domcontentloaded' });
  await fillLikelySequenceField(page, sequence, [
    'input[type="search"]',
    'input[placeholder*="Search" i]',
    'textarea[placeholder*="sequence" i]',
  ]);
  await page.keyboard.press('Enter').catch(() => {});
}

async function openAlphaFold(page, fasta) {
  await page.goto('https://alphafoldserver.com', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);
  await fillLikelySequenceField(page, fasta);
}

async function openHHpred(page, fasta) {
  await page.goto('https://toolkit.tuebingen.mpg.de/tools/hhpred', { waitUntil: 'domcontentloaded' });
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(3000);
  await fillLikelySequenceField(page, fasta, [
    'textarea[name*="sequence" i]',
    'textarea[id*="sequence" i]',
    'textarea[placeholder*="sequence" i]',
    'textarea[placeholder*="fasta" i]',
    'textarea',
  ]);
}

async function openFoldMason(page, files) {
  await page.goto('https://search.foldseek.com/foldmason', { waitUntil: 'domcontentloaded' });
  await uploadLikelyFiles(page, files || []);
}

async function openDali(page, files) {
  await page.goto('https://ekhidna2.biocenter.helsinki.fi/dali/', { waitUntil: 'domcontentloaded' });
  await uploadLikelyFiles(page, files || []);
}

async function openVast(page, files) {
  await page.goto('https://www.ncbi.nlm.nih.gov/Structure/VAST/vastsearch.html', {
    waitUntil: 'domcontentloaded',
  });
  await uploadLikelyFiles(page, files || []);
}

async function openPDBeFold(page, files) {
  await page.goto('https://www.ebi.ac.uk/msd-srv/ssm/', { waitUntil: 'domcontentloaded' });
  await uploadLikelyFiles(page, files || []);
}

async function openPisa(page, files) {
  await page.goto('https://www.ebi.ac.uk/pdbe/prot_int/', { waitUntil: 'domcontentloaded' });
  await uploadLikelyFiles(page, files || []);
}

async function openCaver(page, files) {
  await page.goto('https://loschmidt.chemi.muni.cz/caverweb/', { waitUntil: 'domcontentloaded' });
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await dismissCommonBanners(page);
  try {
    await page.getByText(/^Upload PDB file$/i).click({ timeout: 5000 });
  } catch {
    try {
      await page.locator('label').filter({ hasText: /Upload PDB file/i }).first().click({ timeout: 3000 });
    } catch {
      // CAVER may already expose the upload input.
    }
  }
  await uploadLikelyFiles(page, files || []);
}

async function openUSalign(page, files) {
  await page.goto('https://aideepmed.com/US-align/', { waitUntil: 'domcontentloaded' });
  await uploadLikelyFiles(page, files || []);
}

async function openFoldDisco(page, files, motif) {
  await page.goto('https://search.foldseek.com/folddisco', { waitUntil: 'domcontentloaded' });
  await uploadLikelyFiles(page, files || []);
  if (motif) {
    await fillLikelyTextField(page, motif, [
      'textarea[placeholder*="residue" i]',
      'input[placeholder*="residue" i]',
      'textarea[placeholder*="motif" i]',
      'input[placeholder*="motif" i]',
    ]);
  }
}

async function selectLikelyOption(page, value) {
  if (!value) return false;
  const needle = String(value).toLowerCase();
  const selects = await page.locator('select').elementHandles();
  for (const handle of selects) {
    const matched = await handle.evaluate((select, needleText) => {
      const options = Array.from(select.options || []);
      const option = options.find((opt) => {
        const text = `${opt.textContent || ''} ${opt.value || ''}`.toLowerCase();
        return text.includes(needleText.toLowerCase());
      });
      if (!option) return false;
      select.value = option.value;
      select.dispatchEvent(new Event('input', { bubbles: true }));
      select.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }, needle);
    if (matched) return true;
  }
  return false;
}

async function openHDockNucleotide(page, files, sequence, nucleotideType) {
  await page.goto('http://hdock.phys.hust.edu.cn/', { waitUntil: 'domcontentloaded' });
  await uploadLikelyFiles(page, files || []);
  await fillLikelyTextField(page, sequence, [
    'textarea[name*="seq" i]',
    'textarea[id*="seq" i]',
    'textarea[placeholder*="sequence" i]',
    'textarea',
  ]);
  await selectLikelyOption(page, nucleotideType).catch(() => false);
}

async function openConsurf(page, fasta) {
  await page.goto('https://colab.research.google.com/drive/1PhDXX7k12oUsV6T_xkXC3Rm9R99e7tHz', {
    waitUntil: 'domcontentloaded',
  });
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page
    .evaluate((value) => {
      window.__CHIMERAX_CONSURF_FASTA__ = value;
      try {
        navigator.clipboard.writeText(value);
      } catch {
        // Clipboard permission is browser-dependent; field filling below is the primary path.
      }
    }, fasta)
    .catch(() => {});
  await page.waitForTimeout(5000);
  await fillLikelySequenceField(page, fasta, [
    'textarea[aria-label*="sequence" i]',
    'textarea[aria-label*="fasta" i]',
    'textarea[aria-label*="protein" i]',
    'textarea[placeholder*="sequence" i]',
    'textarea[placeholder*="fasta" i]',
    'textarea[placeholder*="protein" i]',
    '[contenteditable="true"][aria-label*="sequence" i]',
    '[contenteditable="true"][aria-label*="fasta" i]',
    '[contenteditable="true"][aria-label*="protein" i]',
    '[contenteditable="true"]',
    'textarea',
  ]);
}

const executablePath = chromePath();
if (!executablePath) {
  console.error(JSON.stringify({ ok: false, error: 'Google Chrome is not installed.' }));
  process.exit(1);
}

const port = await freePort();
const userDataDir = mkdtempSync(join(tmpdir(), 'chimerax-codex-bridge-chrome-'));
spawn(
  executablePath,
  [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${userDataDir}`,
    '--new-window',
    'about:blank',
  ],
  {
    detached: true,
    stdio: 'ignore',
  }
).unref();

await waitForDebugger(port);
const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const context = browser.contexts()[0];
if ((payload.sites || []).includes('consurf')) {
  await context
    .grantPermissions(['clipboard-read', 'clipboard-write'], {
      origin: 'https://colab.research.google.com',
    })
    .catch(() => {});
}

const opened = [];
for (const site of payload.sites || []) {
  const page = await context.newPage();
  if (site === 'uniprot') {
    await fillUniProt(page, payload.fasta);
    opened.push('UniProt BLAST');
  } else if (site === 'ncbi') {
    await fillNCBI(page, payload.fasta);
    opened.push('NCBI BLASTP');
  } else if (site === 'hmmer') {
    await fillHmmer(page, payload.fasta);
    opened.push('HMMER phmmer');
  } else if (site === 'interpro') {
    await fillInterPro(page, payload.fasta);
    opened.push('InterPro / Pfam');
  } else if (site === 'rcsb') {
    await fillRcsb(page, payload.fasta);
    opened.push('RCSB sequence search');
  } else if (site === 'alphafold') {
    await openAlphaFold(page, payload.fasta);
    opened.push('AlphaFold Server');
  } else if (site === 'hhpred') {
    await openHHpred(page, payload.fasta);
    opened.push('HHpred / HHblits');
  } else if (site === 'foldmason') {
    await openFoldMason(page, payload.structureFiles || []);
    opened.push('FoldMason');
  } else if (site === 'dali') {
    await openDali(page, payload.structureFiles || []);
    opened.push('DALI');
  } else if (site === 'vast') {
    await openVast(page, payload.structureFiles || []);
    opened.push('NCBI VAST');
  } else if (site === 'pdbefold') {
    await openPDBeFold(page, payload.structureFiles || []);
    opened.push('PDBeFold / SSM');
  } else if (site === 'pisa') {
    await openPisa(page, payload.structureFiles || []);
    opened.push('PDBePISA');
  } else if (site === 'caver') {
    await openCaver(page, payload.structureFiles || []);
    opened.push('CAVER Web');
  } else if (site === 'usalign') {
    await openUSalign(page, payload.structureFiles || []);
    opened.push('US-align');
  } else if (site === 'folddisco') {
    await openFoldDisco(page, payload.structureFiles || [], payload.motif || '');
    opened.push('FoldDisco');
  } else if (site === 'hdock-nucleotide') {
    await openHDockNucleotide(
      page,
      payload.structureFiles || [],
      payload.nucleotideSequence || payload.fasta || '',
      payload.nucleotideType || ''
    );
    opened.push('HDOCK nucleotide docking');
  } else if (site === 'consurf') {
    await openConsurf(page, payload.fasta);
    opened.push('ConSurf Colab');
  }
}

console.log(JSON.stringify({ ok: true, opened }));
