export interface Point {
  x: number;
  y: number;
}

export interface DroneState {
  id: string;
  name: string;
  x: number;
  y: number;
  targetX: number;
  targetY: number;
  vx: number;
  vy: number;
  battery: number;
  signal: number;
  status: 'IDLE' | 'BIDDING' | 'TAKEOFF' | 'MISSION' | 'RTL' | 'LANDED' | 'OFFLINE' | 'HOLD';
  altitude: number;
  speed: number;
  temperature: number;
  payload: string;
  color: string;
  taskIds: string[];
  currentTaskIndex: number;
  waypoints: Point[];
  orcaActive: boolean;
  failsafeActive: boolean;
  comsLost: boolean;
}

export interface SwarmTask {
  id: string;
  name: string;
  x: number;
  y: number;
  type: string;
  allocatedDroneId: string | null;
  bidValue: number;
  completed: boolean;
}

export interface Obstacle {
  id: string;
  x: number;
  y: number;
  radius: number;
  type: 'CRANE' | 'BIRD' | 'STRUCTURE' | 'STORM';
}

/**
 * Generates grid sweep waypoints (lawnmower pattern) inside a bounding area.
 */
export function generateSearchGrid(
  bounds: Point[],
  spacing: number = 40
): { tasks: SwarmTask[]; waypoints: Point[] } {
  if (bounds.length < 3) return { tasks: [], waypoints: [] };

  // Calculate simple bounding box
  const xs = bounds.map(p => p.x);
  const ys = bounds.map(p => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);

  const waypoints: Point[] = [];
  const tasks: SwarmTask[] = [];

  let taskIdCounter = 1;
  let direction = 1;

  for (let x = minX + spacing / 2; x <= maxX; x += spacing) {
    const yStart = direction === 1 ? minY + spacing / 2 : maxY - spacing / 2;
    const yEnd = direction === 1 ? maxY - spacing / 2 : minY + spacing / 2;

    // Generate points along this vertical scan line
    const steps = 4;
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      const py = yStart + (yEnd - yStart) * t;
      
      // Simple inside-polygon test (ray casting) if needed, 
      // but for simplicity of simulation we clamp to bounds.
      waypoints.push({ x, y: py });
      
      // Create tasks at intervals
      if (i === 1 || i === 3) {
        tasks.push({
          id: `task-${taskIdCounter++}`,
          name: `Scan Zone ${taskIdCounter - 1}`,
          x: x,
          y: py,
          type: 'SCAN',
          allocatedDroneId: null,
          bidValue: 0,
          completed: false,
        });
      }
    }
    direction *= -1; // alternate scanning direction
  }

  return { tasks, waypoints };
}

/**
 * Simulates a Consensus-Based Bundle Algorithm (CBBA) for decentralized task allocation.
 * In CBBA, each drone bids on a bundle of tasks based on proximity, battery and capabilities,
 * then communicates with neighbors to resolve conflicts.
 */
export function runCBBASimulation(
  drones: DroneState[],
  tasks: SwarmTask[]
): { allocations: Record<string, string[]>; bids: Record<string, Record<string, number>> } {
  const allocations: Record<string, string[]> = {};
  const bids: Record<string, Record<string, number>> = {};

  drones.forEach(d => {
    allocations[d.id] = [];
    bids[d.id] = {};
  });

  if (tasks.length === 0 || drones.length === 0) return { allocations, bids };

  // 1. Compute bids for all drones on all tasks
  // Bids are influenced by distance, battery health, and matching payload
  tasks.forEach(task => {
    drones.forEach(drone => {
      if (drone.status === 'OFFLINE' || drone.battery < 20) {
        bids[drone.id][task.id] = 0; // cannot bid
        return;
      }

      const dx = task.x - drone.x;
      const dy = task.y - drone.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      // Base bid starts at 100
      let bid = 100 - dist * 0.15;

      // Add battery bonus
      bid += (drone.battery / 100) * 20;

      // Payload bonus (SAR matches SCAN, Agri matches SPRAY, etc.)
      const isSprayTask = task.type === 'SPRAY' && drone.payload === 'Agri Spray';
      const isScanTask = task.type === 'SCAN' && drone.payload === 'Thermal Scanner';
      const isInspection = task.type === 'INSPECT' && drone.payload === '4K Gimbal';
      
      if (isSprayTask || isScanTask || isInspection) {
        bid += 30; // payload compatibility bonus
      }

      bids[drone.id][task.id] = Math.max(1, bid);
    });
  });

  // 2. Resolve allocations (consensus simulation)
  // Each task goes to the highest bidder
  const taskWinners: Record<string, { droneId: string; bid: number }> = {};

  tasks.forEach(task => {
    let highestBid = 0;
    let winningDroneId = '';

    drones.forEach(drone => {
      const currentBid = bids[drone.id][task.id] || 0;
      if (currentBid > highestBid) {
        highestBid = currentBid;
        winningDroneId = drone.id;
      }
    });

    if (winningDroneId) {
      taskWinners[task.id] = { droneId: winningDroneId, bid: highestBid };
    }
  });

  // 3. Assemble bundles for each drone
  // Sort tasks for each drone based on proximity to optimize flight path
  Object.entries(taskWinners).forEach(([taskId, winner]) => {
    allocations[winner.droneId].push(taskId);
  });

  // Sort each drone's bundle so tasks are executed in a logical sequence (nearest first)
  drones.forEach(drone => {
    const droneTasks = allocations[drone.id];
    droneTasks.sort((a, b) => {
      const taskA = tasks.find(t => t.id === a)!;
      const taskB = tasks.find(t => t.id === b)!;
      const distA = Math.hypot(taskA.x - drone.x, taskA.y - drone.y);
      const distB = Math.hypot(taskB.x - drone.x, taskB.y - drone.y);
      return distA - distB;
    });
  });

  return { allocations, bids };
}

/**
 * Computes Optimal Reciprocal Collision Avoidance (ORCA) local safety adjustments.
 * This simulates local inter-drone collision avoidance and static obstacle avoidance.
 */
export function calculateORCAVelocity(
  currentDrone: DroneState,
  allDrones: DroneState[],
  obstacles: Obstacle[]
): { vx: number; vy: number; orcaActive: boolean } {
  // Target velocity (where the drone WANTS to go)
  const dx = currentDrone.targetX - currentDrone.x;
  const dy = currentDrone.targetY - currentDrone.y;
  const distToTarget = Math.sqrt(dx * dx + dy * dy);

  if (distToTarget < 5 || currentDrone.status === 'HOLD' || currentDrone.status === 'OFFLINE') {
    return { vx: 0, vy: 0, orcaActive: false };
  }

  // Max Speed limits
  const maxSpeed = currentDrone.speed;
  let prefVx = (dx / distToTarget) * maxSpeed;
  let prefVy = (dy / distToTarget) * maxSpeed;

  let avoidVx = 0;
  let avoidVy = 0;
  let orcaActive = false;
  let collisionCount = 0;

  const safetyRadius = 24; // inter-drone safety distance
  const obstacleSafetyRadius = 30; // obstacle avoidance distance

  // 1. Avoid other drones
  allDrones.forEach(other => {
    if (other.id === currentDrone.id || other.status === 'OFFLINE' || other.status === 'LANDED') return;

    const separationX = currentDrone.x - other.x;
    const separationY = currentDrone.y - other.y;
    const distance = Math.sqrt(separationX * separationX + separationY * separationY);

    if (distance < safetyRadius * 2) {
      orcaActive = true;
      collisionCount++;
      
      // Calculate repulsive force
      const forceStrength = (safetyRadius * 2 - distance) / (distance + 0.1);
      
      // Push vector: normal to the line of contact, or slightly offset to rotate around each other
      // Adding a slight rotational component creates the "reciprocal" bypass flow (drones pass on left/right)
      const pushX = (separationX / distance) * 2.5 + (-separationY / distance) * 0.8;
      const pushY = (separationY / distance) * 2.5 + (separationX / distance) * 0.8;

      avoidVx += pushX * forceStrength * maxSpeed;
      avoidVy += pushY * forceStrength * maxSpeed;
    }
  });

  // 2. Avoid static obstacles
  obstacles.forEach(obs => {
    const separationX = currentDrone.x - obs.x;
    const separationY = currentDrone.y - obs.y;
    const distance = Math.sqrt(separationX * separationX + separationY * separationY);
    const combinedRadius = obs.radius + obstacleSafetyRadius;

    if (distance < combinedRadius) {
      orcaActive = true;
      collisionCount++;

      // Repulsive vector away from obstacle
      const forceStrength = (combinedRadius - distance) / (distance + 0.1);
      
      // Strongly push away and add lateral bypass velocity
      const pushX = (separationX / distance) * 3.5 + (-separationY / distance) * 1.5;
      const pushY = (separationY / distance) * 3.5 + (separationX / distance) * 1.5;

      avoidVx += pushX * forceStrength * maxSpeed;
      avoidVy += pushY * forceStrength * maxSpeed;
    }
  });

  // 3. Blend target and avoidance velocities
  if (orcaActive && collisionCount > 0) {
    // Average out avoidance velocities
    const avgAvoidVx = avoidVx / collisionCount;
    const avgAvoidVy = avoidVy / collisionCount;

    // Output velocity is a blend (e.g. 60% avoidance, 40% target)
    let finalVx = prefVx * 0.35 + avgAvoidVx * 0.65;
    let finalVy = prefVy * 0.35 + avgAvoidVy * 0.65;

    // Clamp speed to maxSpeed
    const finalSpeed = Math.sqrt(finalVx * finalVx + finalVy * finalVy);
    if (finalSpeed > maxSpeed) {
      finalVx = (finalVx / finalSpeed) * maxSpeed;
      finalVy = (finalVy / finalSpeed) * maxSpeed;
    }

    return { vx: finalVx, vy: finalVy, orcaActive: true };
  }

  return { vx: prefVx, vy: prefVy, orcaActive: false };
}
