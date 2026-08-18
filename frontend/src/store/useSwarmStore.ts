import { create } from 'zustand';
import type { 
  Point, 
  DroneState, 
  SwarmTask, 
  Obstacle 
} from '../utils/swarmAlgorithms';
import { 
  runCBBASimulation, 
  calculateORCAVelocity, 
  generateSearchGrid 
} from '../utils/swarmAlgorithms';

export type UserRole = 'COMMANDER' | 'OPERATOR' | 'OBSERVER';

export interface LogEvent {
  id: string;
  timestamp: string;
  category: 'SYSTEM' | 'TELEMETRY' | 'ALGORITHM' | 'AI' | 'FAILSAFE';
  message: string;
  severity: 'INFO' | 'WARNING' | 'CRITICAL';
}

interface EdgeNetwork {
  edgeServerConnected: boolean;
  lteConnected: boolean;
  wifiMeshConnected: boolean;
  loraFallbackActive: boolean;
  latencyMs: number;
}

interface SwarmStore {
  // Navigation & Permissions
  activeTab: 'OPERATIONS' | 'DIGITAL_TWIN' | 'ANALYTICS' | 'SETTINGS';
  role: UserRole;
  setActiveTab: (tab: 'OPERATIONS' | 'DIGITAL_TWIN' | 'ANALYTICS' | 'SETTINGS') => void;
  setRole: (role: UserRole) => void;

  // Swarm States
  drones: DroneState[];
  tasks: SwarmTask[];
  obstacles: Obstacle[];
  boundaries: Point[];
  baseStation: Point;
  missionType: 'SAR' | 'AGRI' | 'INSPECT' | 'PATROL' | 'NONE';
  missionStatus: 'IDLE' | 'BIDDING' | 'LAUNCHING' | 'ACTIVE' | 'RTL' | 'HOLD' | 'COMPLETED';
  missionTimer: number; // in seconds
  coveragePercentage: number;
  swarmHealthScore: number;
  edgeNetwork: EdgeNetwork;
  weather: {
    windSpeedKph: number;
    rainMm: number;
    fogDensityPct: number;
  };
  eventLogs: LogEvent[];

  // Actions
  setMissionType: (type: 'SAR' | 'AGRI' | 'INSPECT' | 'PATROL' | 'NONE') => void;
  setBoundaries: (bounds: Point[]) => void;
  addObstacle: (obstacle: Obstacle) => void;
  clearObstacles: () => void;
  toggleEdgeNetwork: (key: keyof EdgeNetwork) => void;
  addLog: (category: LogEvent['category'], message: string, severity: LogEvent['severity']) => void;
  clearLogs: () => void;
  setWeather: (wind: number, rain: number, fog: number) => void;
  
  // Mission Operations
  startBidding: () => void;
  launchSwarm: () => void;
  abortAndRTL: () => void;
  holdSwarm: () => void;
  killSwarm: () => void;
  resetPlatform: () => void;

  // Drone Failsafe Injection
  injectComsLoss: (droneId: string) => void;
  injectLowBattery: (droneId: string) => void;
  restoreDrone: (droneId: string) => void;

  // Physics simulation step tick
  simulationStep: () => void;
}

// Initial Drone Setup
const initialDrones = (): DroneState[] => [
  {
    id: 'drone-01',
    name: 'Valkyrie-Alpha',
    x: 400,
    y: 450,
    targetX: 400,
    targetY: 450,
    vx: 0,
    vy: 0,
    battery: 98,
    signal: 95,
    status: 'IDLE',
    altitude: 0,
    speed: 12, // units per tick
    temperature: 34,
    payload: 'Thermal Scanner',
    color: '#00f0ff', // Cyber Blue
    taskIds: [],
    currentTaskIndex: 0,
    waypoints: [],
    orcaActive: false,
    failsafeActive: false,
    comsLost: false,
  },
  {
    id: 'drone-02',
    name: 'Demeter-Beta',
    x: 420,
    y: 455,
    targetX: 420,
    targetY: 455,
    vx: 0,
    vy: 0,
    battery: 99,
    signal: 92,
    status: 'IDLE',
    altitude: 0,
    speed: 10,
    temperature: 32,
    payload: 'Agri Spray',
    color: '#39ff14', // Neon Green
    taskIds: [],
    currentTaskIndex: 0,
    waypoints: [],
    orcaActive: false,
    failsafeActive: false,
    comsLost: false,
  },
  {
    id: 'drone-03',
    name: 'Specter-Gamma',
    x: 380,
    y: 455,
    targetX: 380,
    targetY: 455,
    vx: 0,
    vy: 0,
    battery: 95,
    signal: 89,
    status: 'IDLE',
    altitude: 0,
    speed: 15,
    temperature: 36,
    payload: '4K Gimbal',
    color: '#ffaa00', // Cyber Orange
    taskIds: [],
    currentTaskIndex: 0,
    waypoints: [],
    orcaActive: false,
    failsafeActive: false,
    comsLost: false,
  },
  {
    id: 'drone-04',
    name: 'Aegis-Delta',
    x: 440,
    y: 460,
    targetX: 440,
    targetY: 460,
    vx: 0,
    vy: 0,
    battery: 96,
    signal: 96,
    status: 'IDLE',
    altitude: 0,
    speed: 11,
    temperature: 33,
    payload: 'Thermal Scanner',
    color: '#d946ef', // Neon Purple
    taskIds: [],
    currentTaskIndex: 0,
    waypoints: [],
    orcaActive: false,
    failsafeActive: false,
    comsLost: false,
  },
];

export const useSwarmStore = create<SwarmStore>((set, get) => ({
  activeTab: 'OPERATIONS',
  role: 'OPERATOR',
  setActiveTab: (tab) => set({ activeTab: tab }),
  setRole: (role) => set({ role }),

  drones: initialDrones(),
  tasks: [],
  obstacles: [
    { id: 'obs-01', x: 250, y: 220, radius: 25, type: 'CRANE' },
    { id: 'obs-02', x: 550, y: 180, radius: 20, type: 'STRUCTURE' },
  ],
  boundaries: [],
  baseStation: { x: 400, y: 450 },
  missionType: 'NONE',
  missionStatus: 'IDLE',
  missionTimer: 0,
  coveragePercentage: 0,
  swarmHealthScore: 100,
  edgeNetwork: {
    edgeServerConnected: true,
    lteConnected: true,
    wifiMeshConnected: true,
    loraFallbackActive: false,
    latencyMs: 14,
  },
  weather: {
    windSpeedKph: 12,
    rainMm: 0,
    fogDensityPct: 5,
  },
  eventLogs: [
    {
      id: 'log-001',
      timestamp: new Date().toLocaleTimeString(),
      category: 'SYSTEM',
      message: 'Drone MatrX Edge Core initialized.',
      severity: 'INFO',
    },
    {
      id: 'log-002',
      timestamp: new Date().toLocaleTimeString(),
      category: 'SYSTEM',
      message: 'Edge Server connection established via Local WiFi Mesh.',
      severity: 'INFO',
    }
  ],

  setMissionType: (type) => {
    set({ missionType: type });
    get().addLog('SYSTEM', `Selected mission type: ${type}`, 'INFO');
  },

  setBoundaries: (bounds) => {
    set({ boundaries: bounds });
    if (bounds.length >= 3) {
      // Auto-generate search grid and task nodes
      const { tasks } = generateSearchGrid(bounds, 50);
      
      // Override tasks types based on current missionType
      const updatedTasks = tasks.map((t, idx) => {
        let taskType = 'SCAN';
        let name = `Thermal Grid ${idx + 1}`;
        if (get().missionType === 'AGRI') {
          taskType = 'SPRAY';
          name = `Crop Spray Sect ${idx + 1}`;
        } else if (get().missionType === 'INSPECT') {
          taskType = 'INSPECT';
          name = `Infra Check Pt ${idx + 1}`;
        } else if (get().missionType === 'PATROL') {
          taskType = 'SCAN';
          name = `Patrol Point ${idx + 1}`;
        }
        return { ...t, type: taskType, name };
      });

      set({ tasks: updatedTasks, missionStatus: 'IDLE' });
      get().addLog('ALGORITHM', `Generated ${updatedTasks.length} search grid sub-regions inside AOI. Ready for bidding.`, 'INFO');
    } else {
      set({ tasks: [] });
    }
  },

  addObstacle: (obstacle) => {
    set((state) => ({ obstacles: [...state.obstacles, obstacle] }));
    get().addLog('SYSTEM', `Injected simulated obstacle at X:${Math.round(obstacle.x)} Y:${Math.round(obstacle.y)}`, 'WARNING');
  },

  clearObstacles: () => {
    set({ obstacles: [] });
    get().addLog('SYSTEM', 'Cleared all tactical obstacles.', 'INFO');
  },

  toggleEdgeNetwork: (key) => {
    set((state) => {
      const updatedNetwork = { ...state.edgeNetwork, [key]: !state.edgeNetwork[key] };
      
      // Calculate dynamic simulated latency
      let latency = 8;
      if (!updatedNetwork.wifiMeshConnected && updatedNetwork.lteConnected) {
        latency = 45; // 4G/LTE latency
      } else if (!updatedNetwork.wifiMeshConnected && !updatedNetwork.lteConnected) {
        updatedNetwork.loraFallbackActive = true;
        latency = 180; // LoRa latency is much higher
      } else {
        updatedNetwork.loraFallbackActive = false;
      }

      if (!updatedNetwork.edgeServerConnected) {
        latency = 0; // completely dead offline (no edge connection)
      }
      
      updatedNetwork.latencyMs = latency;

      return { edgeNetwork: updatedNetwork };
    });

    const isConnected = get().edgeNetwork[key];
    const statusText = isConnected ? 'ENABLED' : 'DISABLED';
    get().addLog('SYSTEM', `Edge Link Network: ${String(key)} has been ${statusText}.`, isConnected ? 'INFO' : 'WARNING');
  },

  addLog: (category, message, severity) => {
    const newLog: LogEvent = {
      id: `log-${Math.random().toString(36).substr(2, 9)}`,
      timestamp: new Date().toLocaleTimeString(),
      category,
      message,
      severity,
    };
    set((state) => ({
      eventLogs: [newLog, ...state.eventLogs].slice(0, 100), // keep last 100 logs
    }));
  },

  clearLogs: () => set({ eventLogs: [] }),

  setWeather: (wind, rain, fog) => {
    set({ weather: { windSpeedKph: wind, rainMm: rain, fogDensityPct: fog } });
    get().addLog('SYSTEM', `Simulation weather updated. Wind: ${wind}kph, Rain: ${rain}mm, Fog: ${fog}%`, 'INFO');
  },

  startBidding: () => {
    if (get().tasks.length === 0) {
      get().addLog('SYSTEM', 'Bidding failed: No Area of Interest (AOI) defined.', 'CRITICAL');
      return;
    }
    
    set({ missionStatus: 'BIDDING' });
    get().addLog('ALGORITHM', 'Initiating Consensus-Based Bundle Algorithm (CBBA) distributed bidding sequence.', 'INFO');
    
    // Simulate CBBA latency
    setTimeout(() => {
      const { allocations, bids } = runCBBASimulation(get().drones, get().tasks);
      
      // Map tasks to drones in state
      set((state) => {
        const updatedDrones = state.drones.map(drone => {
          if (drone.status === 'OFFLINE') return drone;
          const assignedTaskIds = allocations[drone.id] || [];
          
          // Generate actual waypoints based on assigned tasks
          const waypoints = assignedTaskIds.map(taskId => {
            const task = state.tasks.find(t => t.id === taskId)!;
            return { x: task.x, y: task.y };
          });

          return {
            ...drone,
            status: 'BIDDING' as const,
            taskIds: assignedTaskIds,
            waypoints: waypoints,
            currentTaskIndex: 0,
            targetX: waypoints[0]?.x ?? drone.x,
            targetY: waypoints[0]?.y ?? drone.y,
          };
        });

        // Set task owners
        const updatedTasks = state.tasks.map(task => {
          let ownerId: string | null = null;
          let highestBid = 0;
          
          Object.entries(bids).forEach(([droneId, droneBids]) => {
            if (droneBids[task.id] > highestBid) {
              highestBid = droneBids[task.id];
              ownerId = droneId;
            }
          });

          return {
            ...task,
            allocatedDroneId: ownerId,
            bidValue: Math.round(highestBid),
          };
        });

        return { drones: updatedDrones, tasks: updatedTasks, missionStatus: 'LAUNCHING' };
      });

      get().addLog('ALGORITHM', 'CBBA bidding phase completed. Task bundles distributed. Swarm waiting for Launch command.', 'INFO');
    }, 1200);
  },

  launchSwarm: () => {
    set((state) => {
      const updatedDrones = state.drones.map(d => {
        if (d.status === 'OFFLINE' || d.taskIds.length === 0) return d;
        return {
          ...d,
          status: 'TAKEOFF' as const,
          altitude: 15, // Take off to 15m safety altitude
        };
      });

      return { drones: updatedDrones, missionStatus: 'ACTIVE', missionTimer: 0 };
    });

    get().addLog('SYSTEM', 'Launch signal broadasted. Swarm taking off. Altitude: 15m.', 'INFO');
  },

  abortAndRTL: () => {
    set((state) => {
      const updatedDrones = state.drones.map(d => {
        if (d.status === 'OFFLINE' || d.status === 'LANDED') return d;
        return {
          ...d,
          status: 'RTL' as const,
          targetX: state.baseStation.x + (Math.random() * 40 - 20), // staggered landing coordinates
          targetY: state.baseStation.y + (Math.random() * 40 - 20),
          altitude: 20, // RTL safety altitude
        };
      });

      return { drones: updatedDrones, missionStatus: 'RTL' };
    });

    get().addLog('FAILSAFE', 'Emergency RTL (Return to Launch) activated for all operational units.', 'CRITICAL');
  },

  holdSwarm: () => {
    set((state) => {
      const updatedDrones = state.drones.map(d => {
        if (d.status === 'OFFLINE' || d.status === 'LANDED') return d;
        return {
          ...d,
          status: 'HOLD' as const,
          targetX: d.x,
          targetY: d.y,
          vx: 0,
          vy: 0,
        };
      });
      return { drones: updatedDrones, missionStatus: 'HOLD' };
    });
    get().addLog('FAILSAFE', 'Swarm execution suspended. Drones hovering in HOLD mode.', 'WARNING');
  },

  killSwarm: () => {
    set((state) => {
      const updatedDrones = state.drones.map(d => {
        return {
          ...d,
          status: 'LANDED' as const,
          altitude: 0,
          vx: 0,
          vy: 0,
        };
      });
      return { drones: updatedDrones, missionStatus: 'COMPLETED' };
    });
    get().addLog('FAILSAFE', 'Emergency swarm TERMINATE command executed. Rotors stopped.', 'CRITICAL');
  },

  resetPlatform: () => {
    set({
      drones: initialDrones(),
      tasks: [],
      boundaries: [],
      missionStatus: 'IDLE',
      missionType: 'NONE',
      missionTimer: 0,
      coveragePercentage: 0,
      swarmHealthScore: 100,
    });
    get().addLog('SYSTEM', 'Mission control reset. Ready for flight plan.', 'INFO');
  },

  injectComsLoss: (droneId) => {
    set((state) => {
      const updatedDrones = state.drones.map(d => {
        if (d.id !== droneId) return d;
        
        // Coms loss shifts to LoRa fallback first. If LoRa fallback fails, triggers auto-RTL.
        const useLoraFallback = state.edgeNetwork.wifiMeshConnected || state.edgeNetwork.lteConnected;
        
        return {
          ...d,
          comsLost: true,
          signal: 0,
          status: useLoraFallback ? d.status : ('RTL' as const),
          targetX: useLoraFallback ? d.targetX : state.baseStation.x,
          targetY: useLoraFallback ? d.targetY : state.baseStation.y,
          failsafeActive: !useLoraFallback,
        };
      });

      return { drones: updatedDrones };
    });

    const droneName = get().drones.find(d => d.id === droneId)?.name;
    const fallbackActive = get().edgeNetwork.wifiMeshConnected || get().edgeNetwork.lteConnected;

    if (fallbackActive) {
      get().addLog('FAILSAFE', `Telemetry lost on ${droneName}. Engaged LoRa backup mesh fallback (<180ms latency).`, 'WARNING');
    } else {
      get().addLog('FAILSAFE', `Total signal loss on ${droneName}. Triggering autonomous failsafe RTL.`, 'CRITICAL');
    }
  },

  injectLowBattery: (droneId) => {
    set((state) => {
      const updatedDrones = state.drones.map(d => {
        if (d.id !== droneId) return d;
        return {
          ...d,
          battery: 12, // Critical battery level
          status: 'RTL' as const,
          targetX: state.baseStation.x,
          targetY: state.baseStation.y,
          altitude: 22,
          failsafeActive: true,
        };
      });

      return { drones: updatedDrones };
    });

    const droneName = get().drones.find(d => d.id === droneId)?.name;
    get().addLog('FAILSAFE', `Critical low battery alert on ${droneName} (12%). Executing battery-aware RTL.`, 'CRITICAL');
  },

  restoreDrone: (droneId) => {
    set((state) => {
      const updatedDrones = state.drones.map(d => {
        if (d.id !== droneId) return d;
        return {
          ...d,
          battery: 95,
          signal: 94,
          comsLost: false,
          failsafeActive: false,
          status: 'IDLE' as const,
          x: state.baseStation.x + (Math.random() * 20 - 10),
          y: state.baseStation.y + (Math.random() * 20 - 10),
          targetX: state.baseStation.x,
          targetY: state.baseStation.y,
          altitude: 0,
        };
      });
      return { drones: updatedDrones };
    });

    const droneName = get().drones.find(d => d.id === droneId)?.name;
    get().addLog('SYSTEM', `${droneName} connection restored. Drone diagnostically cleared for service.`, 'INFO');
  },

  simulationStep: () => {
    const state = get();
    if (state.missionStatus !== 'ACTIVE' && state.missionStatus !== 'RTL' && state.missionStatus !== 'HOLD') return;

    // Increment mission timer
    set((state) => ({ missionTimer: state.missionTimer + 1 }));

    // Weather impact multipliers
    const windEffect = state.weather.windSpeedKph * 0.05;
    const rainEffect = state.weather.rainMm * 0.15;
    const weatherSpeedLimitMultiplier = Math.max(0.4, 1 - (windEffect + rainEffect) * 0.05);

    let completedTasksCount = 0;

    const updatedDrones = state.drones.map(drone => {
      if (drone.status === 'OFFLINE' || drone.status === 'LANDED') return drone;

      // 1. Telemetry and battery degradation
      const isRTL = drone.status === 'RTL';
      
      let batteryCost = 0.08;
      if (drone.status === 'TAKEOFF') batteryCost = 0.15;
      if (isRTL) batteryCost = 0.1;
      
      const newBattery = Math.max(0, drone.battery - batteryCost);
      const newAltitude = drone.status === 'TAKEOFF' 
        ? Math.min(15, drone.altitude + 1)
        : (isRTL && Math.hypot(drone.x - state.baseStation.x, drone.y - state.baseStation.y) < 10)
          ? Math.max(0, drone.altitude - 1)
          : drone.altitude;

      // Battery failsafe trigger (if not already triggered)
      if (newBattery < 15 && !drone.failsafeActive && drone.status !== 'RTL') {
        setTimeout(() => get().injectLowBattery(drone.id), 0);
      }

      // Check landing condition
      if (isRTL && newAltitude <= 0.1 && Math.hypot(drone.x - state.baseStation.x, drone.y - state.baseStation.y) < 15) {
        return {
          ...drone,
          battery: Math.round(newBattery),
          altitude: 0,
          status: 'LANDED' as const,
          vx: 0,
          vy: 0,
        };
      }

      // 2. Adjust target coordinate based on active task waypoints
      let targetX = drone.targetX;
      let targetY = drone.targetY;
      let status = drone.status;
      let currentTaskIdx = drone.currentTaskIndex;

      if (status === 'TAKEOFF' && newAltitude >= 14.5) {
        status = 'MISSION';
      }

      if (status === 'MISSION') {
        const currentTaskId = drone.taskIds[currentTaskIdx];
        if (currentTaskId) {
          const currentTask = state.tasks.find(t => t.id === currentTaskId)!;
          targetX = currentTask.x;
          targetY = currentTask.y;

          // Check if task is reached
          const distToTask = Math.hypot(drone.x - currentTask.x, drone.y - currentTask.y);
          if (distToTask < 10) {
            // Task completed! Mark task in store
            set((state) => {
              const updatedTasks = state.tasks.map(t => 
                t.id === currentTaskId ? { ...t, completed: true } : t
              );
              return { tasks: updatedTasks };
            });

            // Trigger AI detection events based on mission type and some probability
            if (Math.random() < 0.15) {
              let aiMsg = '';
              if (state.missionType === 'SAR') {
                aiMsg = `AI detection: Person spotted at grid sector coordinates (${Math.round(drone.x)}, ${Math.round(drone.y)}) with 94% confidence.`;
              } else if (state.missionType === 'AGRI') {
                aiMsg = `AI crop health check: Chlorophyll stress detected at coordinate (${Math.round(drone.x)}, ${Math.round(drone.y)}).`;
              } else if (state.missionType === 'INSPECT') {
                aiMsg = `AI detection: Thermal anomaly (electrical insulator hot spot) spotted on steel tower grid.`;
              } else {
                aiMsg = `AI security patrol: Intruder vehicle located entering perimeter fence area.`;
              }
              get().addLog('AI', aiMsg, 'INFO');
            }

            // Move to next task
            currentTaskIdx++;
            if (currentTaskIdx < drone.taskIds.length) {
              const nextTaskId = drone.taskIds[currentTaskIdx];
              const nextTask = state.tasks.find(t => t.id === nextTaskId)!;
              targetX = nextTask.x;
              targetY = nextTask.y;
            } else {
              // Completed all tasks in bundle! Return to base station
              status = 'RTL';
              targetX = state.baseStation.x + (Math.random() * 30 - 15);
              targetY = state.baseStation.y + (Math.random() * 30 - 15);
              get().addLog('SYSTEM', `${drone.name} finished task bundle. Commencing Return to Launch.`, 'INFO');
            }
          }
        } else {
          // No tasks assigned? Go to RTL
          status = 'RTL';
          targetX = state.baseStation.x;
          targetY = state.baseStation.y;
        }
      }

      // 3. Apply ORCA local obstacle/collision avoidance physics
      const tempDrone = { ...drone, targetX, targetY, speed: drone.speed * weatherSpeedLimitMultiplier };
      const { vx, vy, orcaActive } = calculateORCAVelocity(
        tempDrone,
        state.drones,
        state.obstacles
      );

      // Trigger log warning if ORCA triggers
      if (orcaActive && !drone.orcaActive) {
        get().addLog('ALGORITHM', `ORCA activated for ${drone.name} (inter-drone separation collision avoidance engaged).`, 'INFO');
      }

      // 4. Update coordinates using computed velocity
      const newX = drone.x + vx;
      const newY = drone.y + vy;

      // Update telemetry parameters
      const currentTemp = Math.min(65, drone.temperature + (vx !== 0 ? 0.05 : -0.1));

      return {
        ...drone,
        x: newX,
        y: newY,
        vx,
        vy,
        targetX,
        targetY,
        status,
        battery: Math.round(newBattery * 10) / 10,
        altitude: Math.round(newAltitude * 10) / 10,
        speed: drone.speed, // store baseline speed limit
        temperature: Math.round(currentTemp * 10) / 10,
        currentTaskIndex: currentTaskIdx,
        orcaActive,
      };
    });

    // Count completed tasks to update coverage percentage
    state.tasks.forEach(t => {
      if (t.completed) completedTasksCount++;
    });
    const totalTasks = state.tasks.length;
    const newCoverage = totalTasks > 0 ? Math.round((completedTasksCount / totalTasks) * 100) : 0;

    // Check if entire mission is complete (all operational drones are landed or hold, and tasks are done)
    const activeDronesCount = updatedDrones.filter(d => d.status === 'MISSION' || d.status === 'TAKEOFF').length;
    let newStatus = state.missionStatus as SwarmStore['missionStatus'];

    if (totalTasks > 0 && completedTasksCount === totalTasks && activeDronesCount === 0 && newStatus === 'ACTIVE') {
      newStatus = 'COMPLETED';
      get().addLog('SYSTEM', 'Swarm Mission fully completed! All sectors covered.', 'INFO');
    }

    // Dynamic Swarm Health Score calculation
    let offlineCount = 0;
    let criticalBatteryCount = 0;
    let totalDronesCount = updatedDrones.length;
    
    updatedDrones.forEach(d => {
      if (d.comsLost) offlineCount++;
      if (d.battery < 20) criticalBatteryCount++;
    });

    const droneLossDeduction = (offlineCount / totalDronesCount) * 40;
    const batteryDeduction = (criticalBatteryCount / totalDronesCount) * 20;
    const healthScore = Math.max(0, Math.round(100 - droneLossDeduction - batteryDeduction));

    if (healthScore !== state.swarmHealthScore && healthScore < 70) {
      if (state.swarmHealthScore >= 70) {
        get().addLog('SYSTEM', `Swarm Health Score dropped to ${healthScore}%. Commander supervision advised.`, 'WARNING');
      }
    }

    set({
      drones: updatedDrones,
      coveragePercentage: newCoverage,
      missionStatus: newStatus,
      swarmHealthScore: healthScore,
    });
  },
}));
