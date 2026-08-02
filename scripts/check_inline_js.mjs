import fs from 'node:fs';

const pages = ['app/static/dashboard.html', 'app/static/infographic.html'];

for (const filename of pages) {
  const html = fs.readFileSync(filename, 'utf8');
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  for (const [index, match] of scripts.entries()) {
    try {
      new Function(match[1]);
    } catch (error) {
      throw new Error(`${filename}: inline script ${index + 1}: ${error.message}`);
    }
  }
  if (/\son[a-z]+\s*=/i.test(html)) {
    throw new Error(`${filename}: inline event-handler attribute violates CSP`);
  }
  console.log(`${filename}: ${scripts.length} inline script(s) valid`);
}

for (const filename of ['app/static/js/security.js', 'app/static/js/events.js']) {
  const source = fs.readFileSync(filename, 'utf8');
  try {
    new Function(source);
  } catch (error) {
    throw new Error(`${filename}: ${error.message}`);
  }
  console.log(`${filename}: syntax valid`);
}
