const CACHE_NAME = 'cime5-shell-v66-image-ocr';
const IMAGE_CACHE = 'cime5-images-v1';
const REQUIRED_SHELL = ['/', '/index.html', '/app.html', '/manifest.json'];
const OPTIONAL_SHELL = ['/cime-logo.svg'];
const IMAGE_HOSTS = new Set([
  'cdn.myanimelist.net',
  's4.anilist.co',
  'media.kitsu.io',
  'media.kitsu.app',
  'image.tmdb.org',
  'static.tvmaze.com'
]);
async function putShell(url,response){
  if(!response||!response.ok||response.type!=='basic')return;
  const cache=await caches.open(CACHE_NAME);
  await cache.put(url,response.clone());
}
function cacheableImage(url,request){
  if(request.destination!=='image')return false;
  if(url.origin===self.location.origin)
    return url.pathname.startsWith('/media/')||url.pathname.startsWith('/groups_media/');
  return IMAGE_HOSTS.has(url.hostname.toLowerCase());
}
async function storeImage(request,response){
  if(!response||(response.type!=='opaque'&&!response.ok))return;
  try{
    const cache=await caches.open(IMAGE_CACHE);
    await cache.put(request,response.clone());
    const keys=await cache.keys();
    if(keys.length>180)await Promise.all(keys.slice(0,keys.length-180).map(key=>cache.delete(key)));
  }catch(_){}
}
self.addEventListener('install',event=>{
  event.waitUntil((async()=>{
    const cache=await caches.open(CACHE_NAME);
    await cache.addAll(REQUIRED_SHELL);
    await Promise.allSettled(OPTIONAL_SHELL.map(async url=>{
      const response=await fetch(url);
      if(response.ok&&response.type==='basic')await cache.put(url,response);
    }));
    await self.skipWaiting();
  })());
});
self.addEventListener('activate',event=>{
  event.waitUntil((async()=>{
    const keys=await caches.keys();
    await Promise.all(keys.filter(key=>key.startsWith('cime5-shell-')&&key!==CACHE_NAME).map(key=>caches.delete(key)));
    await self.clients.claim();
  })());
});
self.addEventListener('fetch',event=>{
  const request=event.request;
  if(request.method!=='GET')return;
  const url=new URL(request.url);
  if(cacheableImage(url,request)){
    event.respondWith((async()=>{
      const cache=await caches.open(IMAGE_CACHE);
      const cached=await cache.match(request);
      try{
        const fresh=await fetch(request);
        await storeImage(request,fresh);
        return fresh;
      }catch(_){
        return cached||Response.error();
      }
    })());
    return;
  }
  if(url.origin!==self.location.origin)return;
  if(url.pathname.startsWith('/api/')||url.pathname.startsWith('/oauth/'))return;
  const isApp=url.pathname==='/app'||url.pathname==='/app/';
  const shell=request.mode==='navigate'||url.pathname==='/'||url.pathname==='/index.html'||isApp||url.pathname==='/app.html'||url.pathname==='/manifest.json'||url.pathname==='/cime-logo.svg';
  if(!shell)return;
  event.respondWith((async()=>{
    const cache=await caches.open(CACHE_NAME);
    const cacheKey=isApp?'/app.html':url.pathname;
    try{
      const fresh=await fetch(request);
      if(fresh.ok&&fresh.type==='basic')await putShell(cacheKey,fresh);
      return fresh;
    }catch(_){
      return (await cache.match(cacheKey))||(isApp?await cache.match('/app.html'):null)||await cache.match('/')||Response.error();
    }
  })());
});
