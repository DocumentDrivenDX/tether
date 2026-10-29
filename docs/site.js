const frames=[['01-ready','A fresh durable offline demo session.'],['02-approval','The tool call waits for explicit approval.'],['03-approved','Explicit approval completes the fabricated tool journey.'],['04-reopened','Reopening the same store restores the completed conversation.']];
for(const button of document.querySelectorAll('[data-step]')) button.addEventListener('click',()=>{const i=Number(button.dataset.step);for(const b of document.querySelectorAll('[data-step]')){b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));}const img=document.querySelector('#journey-frame');img.src=`media/${frames[i][0]}.svg`;document.querySelector("#journey-full").href=img.src;img.alt=`Actual Tether terminal UI: ${frames[i][1]}`;document.querySelector('#frame-count').textContent=`0${i+1}—04`;document.querySelector('#frame-caption').textContent=`0${i+1} / ${frames[i][1]}`;});
const installs={library:["uv pip install 'novirhiza-tether==0.1.0'",'PYTHON 3.11 ENVIRONMENT','Import as tether. Provider SDKs are excluded from the base installation.'],demo:["uvx --python 3.11 --from 'novirhiza-tether[tui]==0.1.0' tether",'OFFLINE REFERENCE CLIENT','Launches the scripted terminal demo with a local JSONL store. No model account is needed.'],model:["uvx --from '.[tui,openai]' tether --openai-api-baseurl=http://localhost:18001/v1",'LOCAL MODEL · UNRELEASED CHECKOUT','Run from the Tether checkout with a local OpenAI-compatible server already running. Discovers the chat model; tools use the current workspace and require approval. These flags are not in 0.1.0.'],databricks:["uvx --from '.[tui,openai]' tether --databricks-profile=work",'DATABRICKS · UNRELEASED CHECKOUT','Run from the Tether checkout. Replace work with an existing signed-in CLI OAuth profile. Derives the URL and authentication, then opens the system.ai chat-model picker. These flags are not in 0.1.0.']};
for(const button of document.querySelectorAll('[data-install]')) button.addEventListener('click',()=>{const item=installs[button.dataset.install];for(const b of document.querySelectorAll('[data-install]')){b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));}document.querySelector('#install-code').textContent=item[0];document.querySelector('#code-label').textContent=item[1];document.querySelector('#install-note').textContent=item[2];document.querySelector('#copy').textContent='Copy ↗';});
document.querySelector('#copy').addEventListener('click',async()=>{const b=document.querySelector('#copy');try{await navigator.clipboard.writeText(document.querySelector('#install-code').textContent);b.textContent='Copied ✓';}catch{b.textContent='Select command below';const range=document.createRange();range.selectNodeContents(document.querySelector('#install-code'));const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);}});

const qwenFrames=[['qwen-01-ready','The live local Qwen model responds READY.'],['qwen-02-approval','A live local model proposal is held for approval.'],['qwen-03-approved','Explicit approval completes the live local-model journey.']];
for(const button of document.querySelectorAll('[data-qwen]')) button.addEventListener('click',()=>{const i=Number(button.dataset.qwen);for(const b of document.querySelectorAll('[data-qwen]')){b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));}const img=document.querySelector('#qwen-frame');img.src=`media/${qwenFrames[i][0]}.svg`;img.alt=`Actual Tether UI: ${qwenFrames[i][1]}`;document.querySelector('#qwen-full').href=img.src;document.querySelector('#qwen-caption').textContent=`0${i+1} / ${qwenFrames[i][1]}`;});

const startupReels={"local": [["local-startup-write_file-pending", "A real local-model file write waits for approval."], ["local-startup-read_file-pending", "A local file read waits for approval."], ["local-startup-bash-pending", "A shell command waits for approval."], ["local-startup-web_search-pending", "A public web search waits for approval."], ["local-startup-web_page_load-pending", "A public web page load waits for approval."]], "databricks": [["databricks-model-picker", "The profile supplies the URL and authentication; choose a discovered model."], ["databricks-startup-ready", "The selected gateway model returns an accepted READY response."]]};
for (const section of document.querySelectorAll('[data-startup]')) {
  const reel = startupReels[section.dataset.startup];
  for (const button of section.querySelectorAll('[data-startup-frame]')) {
    button.addEventListener('click', () => {
      const frame = reel[Number(button.dataset.startupFrame)];
      for (const other of section.querySelectorAll('[data-startup-frame]')) {
        other.classList.toggle('selected', other === button);
        other.setAttribute('aria-pressed', String(other === button));
      }
      const image = section.querySelector('[data-startup-image]');
      image.src = `media/${frame[0]}.svg`;
      image.alt = `Actual Tether TUI: ${frame[1]}`;
      section.querySelector('[data-startup-full]').href = image.src;
      section.querySelector('[data-startup-caption]').textContent = frame[1];
    });
  }
}
