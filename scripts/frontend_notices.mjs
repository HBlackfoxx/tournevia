// Collect original license text from every installed production lockfile entry.
import {readFile, readdir, mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..','web');
const lock=JSON.parse(await readFile(path.join(root,'package-lock.json'),'utf8'));
const inventory=[];
const texts=['Tournevia browser dependencies\nOriginal upstream notices follow. Includes production dependencies, not only code retained by the bundler.\n'];
for(const [location,entry] of Object.entries(lock.packages).sort(([a],[b])=>a.localeCompare(b))) {
  if(!location||entry.dev)continue;
  const directory=path.join(root,location);
  const metadata=JSON.parse(await readFile(path.join(directory,'package.json'),'utf8'));
  if(metadata.version!==entry.version)throw new Error(`Installed version differs from lock: ${location}`);
  const files=(await readdir(directory)).filter(name=>/^(licen[cs]e|copying|notice|authors)(\.|$)/i.test(name)).sort();
  // This exact package publishes its complete MIT notice inside README.md.
  if(metadata.name==='murmurhash-js'&&metadata.version==='1.0.0'&&!files.length)files.push('README.md');
  if(!files.length)throw new Error(`No license text found for ${location}; review before distributing.`);
  inventory.push({name:metadata.name,version:entry.version,license:entry.license??metadata.license??'See original notices',integrity:entry.integrity,files});
  texts.push(`\n===== ${metadata.name} ${entry.version} =====\n`);
  for(const name of files)texts.push(`\n--- ${name} ---\n${await readFile(path.join(directory,name),'utf8')}\n`);
}
const output=path.join(root,'dist','assets');
await mkdir(output,{recursive:true});
await writeFile(path.join(output,'THIRD_PARTY_NOTICES.txt'),texts.join(''),'utf8');
await writeFile(path.join(output,'dependency-inventory.json'),JSON.stringify(inventory,null,2)+'\n','utf8');
console.log(`Preserved original notices for ${inventory.length} production npm packages.`);
