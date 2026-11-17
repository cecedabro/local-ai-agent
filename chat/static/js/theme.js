(function(){
  var root = document.documentElement;
  function apply(t){
    if(t==="dark"){root.style.setProperty("--bg","#111");root.style.setProperty("--fg","#ddd")}
    else if(t==="light"){root.style.setProperty("--bg","#fff");root.style.setProperty("--fg","#111")}
  }
  var stored = localStorage.getItem("theme");
  apply(stored || "auto");
})();
