function initSearch(){
  const q=document.getElementById("q"),body=document.getElementById("results"),empty=document.getElementById("empty");
  let t;
  async function run(){
    const r=await fetch(q.dataset.url+"?q="+encodeURIComponent(q.value));
    const {results}=await r.json();
    body.innerHTML="";empty.hidden=results.length>0;
    results.forEach(s=>{
      const tr=document.createElement("tr"),owing=parseFloat(s.balance)>0;
      const cells=[s.no,null,s.programme,s.status,CUR+s.balance];
      cells.forEach((c,i)=>{const td=document.createElement("td");
        if(i===1){const a=document.createElement("a");a.href=BASE+s.id+"/";a.textContent=s.name;td.appendChild(a)}
        else td.textContent=c;
        if(i===4){td.className="r";td.style.color=owing?"var(--bad)":"var(--ok)";td.style.fontWeight="600"}
        tr.appendChild(td)});
      body.appendChild(tr)});
  }
  q.addEventListener("input",()=>{clearTimeout(t);t=setTimeout(run,200)});run();
}
