import { useEffect, useRef, useState } from 'react'
import { ChevronRight, Menu, Search, X } from 'lucide-react'

const navigation = ['Mission', 'Flight Plan', 'Analyse', 'Fleet', 'Reports', 'Settings']

const propellers = [
  { id: 1, top: '43%', left: '34%', width: '9%', height: '3%', direction: 'spin-cw' },      // Top-left back
  { id: 2, top: '23%', left: '67.5%', width: '11%', height: '3%', direction: 'spin-ccw' },  // Top-right back
  { id: 3, top: '47%', left: '86%', width: '11%', height: '3%', direction: 'spin-cw' },    // Right outer
  { id: 4, top: '68%', left: '55.5%', width: '10%', height: '3%', direction: 'spin-ccw' },  // Front-right inner
  { id: 5, top: '74.5%', left: '38%', width: '10%', height: '3%', direction: 'spin-cw' },   // Front-left inner
  { id: 6, top: '79%', left: '4%', width: '15%', height: '4%', direction: 'spin-ccw' },     // Bottom-left outer
]

export function LandingPage() {
  const [menuOpen, setMenuOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [clock, setClock] = useState('')
  const droneRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const updateClock = () => setClock(new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date()).replace(/:/g, ' : '))
    updateClock()
    const timer = window.setInterval(updateClock, 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    let animationFrame = 0
    const navBarHeight = 7.2 * 16 // approximate command bar height (7.2rem)

    // Initial default positions (right/center of hero)
    let targetX = window.innerWidth * 0.65
    let targetY = (window.innerHeight - navBarHeight) * 0.45

    let mouseX = targetX
    let mouseY = targetY

    let currentX = targetX
    let currentY = targetY
    let vx = 0
    let vy = 0

    const stiffness = 0.035 // Spring constant
    const damping = 0.88   // Friction damping
    const mass = 1.0       // Mass

    let time = 0

    const updateDrone = () => {
      time += 0.04

      // Wind turbulence noise simulation
      const windX = Math.sin(time) * Math.cos(time * 0.7) * 0.5
      const windY = Math.cos(time * 1.3) * Math.sin(time * 0.5) * 0.5

      // Force calculations (F = stiffness * displacement + turbulence)
      let fx = (mouseX - currentX) * stiffness + windX
      let fy = (mouseY - currentY) * stiffness + windY

      // Acceleration (a = F/m)
      const ax = fx / mass
      const ay = fy / mass

      // Update velocity and apply friction damping
      vx = (vx + ax) * damping
      vy = (vy + ay) * damping

      // Update positions
      currentX += vx
      currentY += vy

      // Measure current dimensions of the drone container
      let w = 600
      let h = 350
      if (droneRef.current) {
        const rect = droneRef.current.getBoundingClientRect()
        if (rect.width > 0) {
          w = rect.width
          h = rect.height
        }
      }

      // Boundaries clamp relative to the hero container
      const heroHeight = window.innerHeight - navBarHeight
      const minX = w / 2
      const maxX = window.innerWidth - w / 2
      const minY = h / 2
      const maxY = heroHeight - h / 2

      currentX = Math.max(minX, Math.min(maxX, currentX))
      currentY = Math.max(minY, Math.min(maxY, currentY))

      // 3D Rotations based on velocities
      const roll = Math.max(-18, Math.min(18, vx * 0.5))       // Bank sideways on X-movement
      const pitch = Math.max(-12, Math.min(12, -vy * 0.4))     // Tilt back/forth on Y-movement
      const yaw = Math.max(-15, Math.min(15, vx * 0.3))        // Turn slightly in direction of travel

      if (droneRef.current) {
        droneRef.current.style.transform = `translate3d(${currentX - w / 2}px, ${currentY - h / 2}px, 0) rotateX(${pitch}deg) rotateY(${yaw}deg) rotateZ(${roll}deg)`
      }

      animationFrame = window.requestAnimationFrame(updateDrone)
    }

    const trackPointer = (event: PointerEvent) => {
      mouseX = event.clientX
      mouseY = event.clientY - navBarHeight
    }

    window.addEventListener('pointermove', trackPointer)
    animationFrame = window.requestAnimationFrame(updateDrone)

    const handleResize = () => {
      mouseX = window.innerWidth * 0.65
      mouseY = (window.innerHeight - navBarHeight) * 0.45
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.cancelAnimationFrame(animationFrame)
      window.removeEventListener('pointermove', trackPointer)
      window.removeEventListener('resize', handleResize)
    }
  }, [])

  return (
    <div className="landing-shell" id="top">
      <header className="command-bar">
        <button className="menu-control" type="button" aria-label="Open navigation" onClick={() => setMenuOpen(true)}><Menu /></button>
        <a href="#top" className="brand" aria-label="Drone Matrx home"><span className="logo-crop"><img src="/drone-matrx-logo.png" alt="" /></span><span>Drone Matrx</span></a>
        <nav className="command-actions" aria-label="Page actions"><button className="search-control" type="button" aria-label="Open search" onClick={() => setSearchOpen((value) => !value)}><span><Search /></span>+ Search</button><button className="nav-label" type="button">Alert</button><time className="clock" aria-live="polite">{clock}</time></nav>
        {searchOpen && <form className="search-panel" onSubmit={(event) => event.preventDefault()}><label htmlFor="global-search">Search Drone Matrx</label><input id="global-search" autoFocus placeholder="Search missions, fleet, reports..." /></form>}
      </header>
      <aside className={`sidebar ${menuOpen ? 'is-open' : ''}`} aria-hidden={!menuOpen}><div className="sidebar-top"><span>Drone Matrx</span><button type="button" aria-label="Close navigation" onClick={() => setMenuOpen(false)}><X /></button></div><nav aria-label="Primary navigation">{navigation.map((item) => <button type="button" key={item} onClick={() => setMenuOpen(false)}><ChevronRight size={18} />{item}</button>)}</nav></aside>
      {menuOpen && <button className="backdrop" type="button" aria-label="Dismiss navigation" onClick={() => setMenuOpen(false)} />}
      <main className="hero" aria-label="Drone Matrx landing page">
        <div className="vertical-brand" aria-hidden="true">011</div>
        <section className="wordmark" aria-label="Drone Matrx"><span>Drone</span><strong>Matrx</strong></section>
        
        {/* Main Drone Container with physical flight simulation & spinning propellers */}
        <div ref={droneRef} className="drone drone-main">
          <img src="/drone-hero.png" alt="Interactive Drone Matrx aircraft" style={{ width: '100%', height: 'auto', pointerEvents: 'none' }} />
          {propellers.map((p) => (
            <div
              key={p.id}
              className={`propeller ${p.direction}`}
              style={{
                position: 'absolute',
                top: p.top,
                left: p.left,
                width: p.width,
                height: p.height,
              }}
            />
          ))}
        </div>

        <img className="drone drone-top" src="/drone-hero.png" alt="" aria-hidden="true" />
        <img className="drone drone-edge" src="/drone-hero.png" alt="" aria-hidden="true" />
        <img className="background-mark" src="/drone-matrx-logo.png" alt="" aria-hidden="true" />
        <p className="future-message">The future of drone<br />fleet management . . .</p>
      </main>
    </div>
  )
}
