#!/usr/bin/env python3
"""
Robustly split admin-dashboard index.html into skeleton + pages/*.html.

Properly tracks <div> nesting by counting ALL occurrences of <div and </div> per line,
including multiple tags on the same line.
"""
import re, os

html_path = 'admin-dashboard/internal/server/static/index.html'
pages_dir = os.path.join(os.path.dirname(html_path), 'pages')

with open(html_path) as f:
    html = f.read()

lines = html.split('\n')

PAGE_RE = re.compile(r'<div\s+x-show="page\s*===\s*\'(\w+)\'"')

# Find exact line ranges for each page section by tracking div nesting
pages = {}

i = 0
while i < len(lines):
    line = lines[i]
    m = PAGE_RE.search(line)
    if m:
        name = m.group(1)
        start = i

        # Count depth: opening div contributes to depth
        depth = 0
        depth += line.count('<div') - line.count('</div>')

        # If there's an opening div on the first line (should always be at least 1)
        # scan until depth returns to 0
        j = i + 1
        while j < len(lines) and depth > 0:
            depth += lines[j].count('<div') - lines[j].count('</div>')
            j += 1

        end = j - 1
        pages[name] = {'start': start, 'end': end, 'name': name}
        print(f"  {name}: lines {start+1}-{end+1} ({end-start+1} lines), depth={depth}")
        i = end + 1
    else:
        i += 1

expected = ['dashboard','tenants','config','tools','rag','agents','abuse','voice','llm','audit']
missing = [p for p in expected if p not in pages]
if missing:
    print(f"ERROR: pages not found: {missing}")
    # Check what we found
    print(f"Found: {list(pages.keys())}")
    sys.exit(1)

# Build set of page line numbers
page_lines = set()
for p in pages.values():
    for i in range(p['start'], p['end'] + 1):
        page_lines.add(i)

# Write page files
os.makedirs(pages_dir, exist_ok=True)
for name in expected:
    p = pages[name]
    content = '\n'.join(lines[p['start']:p['end']+1])
    out_path = os.path.join(pages_dir, f'{name}.html')
    with open(out_path, 'w') as f:
        f.write(content)
    n = content.count('\n') + 1
    print(f"  -> pages/{name}.html ({n} lines)")

# Build skeleton: keep non-page lines, insert markers BEFORE the page region
skeleton_lines = []
for i, line in enumerate(lines):
    for name, p in pages.items():
        if p['start'] == i:
            skeleton_lines.append(f'<!--PAGE:{name}-->')

    if i not in page_lines:
        skeleton_lines.append(line)

skeleton = '\n'.join(skeleton_lines)

skel_count = skeleton.count('\n') + 1
orig_count = len(lines)
print(f"\nSkeleton: {skel_count} lines (was {orig_count})")
print(f"Saved: {orig_count - skel_count} lines")

marker_count = skeleton.count('<!--PAGE:')
page_file_count = len(os.listdir(pages_dir))
print(f"Markers: {marker_count}, page files: {page_file_count}")

with open(html_path, 'w') as f:
    f.write(skeleton)

print("Done!")
