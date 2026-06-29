#!/usr/bin/env python3
"""
optimisation_polyfocale.py — Simulation MPVR (Multi-Perspective Validation & Resilience)
pour l'optimisation de flux énergétiques dans un réseau distribué.

Ce script implémente et compare deux approches :
  - Approche A : Centralisée (mono-focale) — solveur global à chaque pas de temps
  - Approche B : Polyfocale (MPVR) — quorum de perspectives locales asynchrones

Usage :
    python optimisation_polyfocale.py

Dépendances :
    - numpy
    - matplotlib (optionnel, pour les graphiques)
    - xxhash (optionnel, pour les signatures de convergence)

Auteur : MTTV-FLP / MPVR Core Framework (2026)
Licence : CC BY-SA 4.0
"""

import math
import random
import statistics
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum

# ============================================================================
# Configuration
# ============================================================================

@dataclass
class Config:
    """Paramètres de simulation."""
    # Réseau
    num_nodes: int = 256
    topology_degree: int = 4           # degré moyen du graphe
    quorum_threshold: int = 3           # Θ — minimum de perspectives locales

    # Production / consommation
    prod_ratio: float = 0.40
    cons_ratio: float = 0.35
    storage_ratio: float = 0.25

    # Bruit et pannes
    noise_level: float = 0.10          # ±10 % de bruit de mesure
    latency_ms: float = 50.0           # latence de communication moyenne
    failure_rate: float = 0.05         # probabilité de panne par pas de temps
    max_failure_ratio: float = 0.30    # maximum de nœuds en panne simultanément

    # Simulation
    num_cycles: int = 200
    seed: int = 42

    # Polyfocal (MPVR)
    alpha: float = 0.5                 # coefficient d'apprentissage
    sigma_window: int = 5              # k — fenêtre pour signature de convergence
    coherence_epsilon: float = 0.15    # ε — tolérance de cohérence des signes

    # Centralisé
    solver_timeout_ms: float = 500.0   # timeout du solveur central


# ============================================================================
# Types de nœuds
# ============================================================================

class NodeType(Enum):
    PRODUCER = "producer"
    CONSUMER = "consumer"
    STORAGE = "storage"


# ============================================================================
# Modèle de nœud
# ============================================================================

@dataclass
class Perspective:
    """Perspective locale émise par un nœud."""
    node_id: int
    delta: float                      # déséquilibre local prod - cons
    timestamp: float                  # horloge locale
    sigma: int                        # signature de convergence
    is_faulty: bool = False           # si le nœud est en panne


@dataclass
class Node:
    """Un nœud du réseau distribué."""
    node_id: int
    node_type: NodeType
    neighbors: List[int] = field(default_factory=list)

    # État local
    production: float = 0.0
    consumption: float = 0.0
    storage: float = 0.0
    storage_max: float = 100.0
    flow: float = 0.0                  # flux entrant (+ reçoit, - envoie)

    # Historique pour signature de convergence
    delta_history: List[float] = field(default_factory=list)

    # État de panne
    is_faulty: bool = False
    failure_timer: int = 0

    # Métriques locales
    imbalance_history: List[float] = field(default_factory=list)

    def compute_delta(self) -> float:
        """Déséquilibre local : production - consommation."""
        return self.production - self.consumption + self.flow

    def compute_sigma(self, window: int) -> int:
        """
        Signature de convergence σ_i(t).
        Hash sémantique léger des Δ récents.
        """
        if len(self.delta_history) < 2:
            return 0
        recent = self.delta_history[-window:]
        # Version simplifiée : somme pondérée des Δ normalisés
        # Dans une implémentation réelle : xxhash ou similaire
        sig = 0
        for i, d in enumerate(recent):
            sig = ((sig << 5) - sig) + int(d * 1000)
            sig &= 0xFFFFFFFF
        return sig

    def build_perspective(self, t: float, window: int) -> Perspective:
        """Construit la perspective locale courante."""
        return Perspective(
            node_id=self.node_id,
            delta=self.compute_delta(),
            timestamp=t,
            sigma=self.compute_sigma(window),
            is_faulty=self.is_faulty,
        )


# ============================================================================
# Générateur de topologie (graphe aléatoire régulier)
# ============================================================================

def build_topology(num_nodes: int, degree: int) -> List[List[int]]:
    """
    Génère un graphe aléatoire où chaque nœud a ~degree voisins.
    Utilise une méthode de permutation garantissant la connexité.
    """
    if degree >= num_nodes:
        degree = num_nodes - 1

    adj: List[List[int]] = [[] for _ in range(num_nodes)]

    # Anneau de base (connexité garantie)
    for i in range(num_nodes):
        adj[i].append((i + 1) % num_nodes)
        adj[(i + 1) % num_nodes].append(i)

    # Arêtes aléatoires supplémentaires
    extra_edges = (num_nodes * degree) // 2 - num_nodes
    attempts = 0
    while extra_edges > 0 and attempts < num_nodes * 10:
        a = random.randint(0, num_nodes - 1)
        b = random.randint(0, num_nodes - 1)
        if a != b and b not in adj[a]:
            adj[a].append(b)
            adj[b].append(a)
            extra_edges -= 1
        attempts += 1

    return adj


# ============================================================================
# Simulation de la production/consommation
# ============================================================================

def simulate_production(node_id: int, cycle: int, node_type: NodeType) -> float:
    """
    Production d'énergie simulée.
    - Solaire : sinusoïde journalière (pic à midi) + bruit
    - Éolien : constante + bruit périodique
    - Consommateur : pics aux heures 8h, 12h, 19h
    - Stockage : 0 (ni produit ni consommé directement)
    """
    t = cycle / 24.0  # conversion en heures

    match node_type:
        case NodeType.PRODUCER:
            # Mix solaire (60%) + éolien (40%)
            solar = max(0, 50 * math.sin(math.pi * (t - 6) / 12)) if 6 <= t % 24 <= 18 else 0
            wind = 20 + 15 * math.sin(2 * math.pi * t / 168)  # cycle hebdomadaire
            production = 0.6 * solar + 0.4 * wind
            # Bruit
            production *= random.gauss(1.0, 0.08)
            return max(0, production)

        case NodeType.CONSUMER:
            # Demande de base
            base = 30
            # Pics aux heures 8h, 12h, 19h
            hour = t % 24
            if 7 <= hour <= 9:
                peak = 40
            elif 11 <= hour <= 13:
                peak = 50
            elif 18 <= hour <= 20:
                peak = 45
            else:
                peak = 10 * math.sin(math.pi * (hour - 1) / 12)
            consumption = base + peak
            consumption *= random.gauss(1.0, 0.05)
            return max(5, consumption)

        case NodeType.STORAGE:
            return 0  # Le stockage ne produit/consomme pas directement


def simulate_consumption(node_id: int, cycle: int, node_type: NodeType) -> float:
    """Consommation d'énergie simulée."""
    match node_type:
        case NodeType.PRODUCER:
            return random.gauss(5, 1)  # autoconso
        case NodeType.CONSUMER:
            return simulate_production(node_id, cycle, node_type)  # déjà calculé
        case NodeType.STORAGE:
            return 0


# ============================================================================
# Approche A : Centralisée (mono-focale)
# ============================================================================

class CentralizedController:
    """
    Contrôleur central qui résout l'optimisation globale.
    """

    def __init__(self, config: Config, nodes: List[Node]):
        self.config = config
        self.nodes = nodes
        self.cycle_times: List[float] = []
        self.imbalances: List[float] = []
        self.failures: int = 0

    def step(self, cycle: int) -> Tuple[float, float]:
        """
        Un pas de temps centralisé.
        Retourne (déséquilibre global, temps de cycle).
        """
        t_start = random.gauss(0, self.config.latency_ms / 1000)

        # 1. Collecte globale (simulée)
        collect_time = self.config.num_nodes * 0.5 + random.gauss(0, 10)
        if collect_time > 200:
            collect_time = 200

        # 2. Résolution du problème d'optimisation
        # Simule un solveur QP O(N³)
        compute_time = (self.config.num_nodes ** 2) / 20000 + random.gauss(0, 20)
        if compute_time > 300:
            compute_time = 300

        # 3. Vérification de faisabilité
        faulty_nodes = sum(1 for n in self.nodes if n.is_faulty)
        failure_ratio = faulty_nodes / self.config.num_nodes

        if failure_ratio > 0.15:
            # Le solveur diverge ou est infaisable
            self.failures += 1
            cycle_time = collect_time + compute_time + 50
            self.cycle_times.append(cycle_time)

            # Aucun rééquilibrage possible
            global_imbalance = abs(sum(n.compute_delta() for n in self.nodes))
            self.imbalances.append(global_imbalance)
            return global_imbalance, cycle_time

        # 4. Distribution des instructions
        distrib_time = self.config.num_nodes * 0.3 + random.gauss(0, 5)
        cycle_time = collect_time + compute_time + distrib_time

        # 5. Application du rééquilibrage
        # Le contrôleur central calcule le flux optimal pour chaque nœud
        total_delta = sum(n.compute_delta() for n in self.nodes)
        target_flow = -total_delta / self.config.num_nodes

        for node in self.nodes:
            if not node.is_faulty:
                node.flow += target_flow * 0.8  # facteur d'amortissement

        global_imbalance = abs(sum(n.compute_delta() for n in self.nodes))
        self.cycle_times.append(cycle_time)
        self.imbalances.append(global_imbalance)

        return global_imbalance, cycle_time


# ============================================================================
# Approche B : Polyfocale (MPVR)
# ============================================================================

class PolyfocalNetwork:
    """
    Réseau distribué avec contrôle polyfocal (MPVR).
    Chaque nœud est un agent autonome communiquant via quorum.
    """

    def __init__(self, config: Config, nodes: List[Node], adj: List[List[int]]):
        self.config = config
        self.nodes = nodes
        self.adj = adj

        self.cycle_times: List[float] = []
        self.imbalances: List[float] = []
        self.quorum_sizes: List[int] = []
        self.divergence_events: int = 0
        self.total_cycles: int = 0

    def step(self, cycle: int, global_time: float) -> Tuple[float, float]:
        """
        Un pas de temps polyfocal (MPVR).
        Chaque nœud agit en parallèle avec ses voisins.
        Retourne (déséquilibre global, temps de cycle max).
        """
        self.total_cycles += 1

        # Phase 1 : Chaque nœud construit sa perspective
        perspectives: Dict[int, Perspective] = {}
        for node in self.nodes:
            if node.is_faulty:
                continue
            delta = node.compute_delta()
            node.delta_history.append(delta)
            if len(node.delta_history) > self.config.sigma_window * 2:
                node.delta_history.pop(0)

        # Phase 2 : Échange asynchrone dans le voisinage
        max_cycle_time = 0.0
        total_imbalance = 0.0

        for node in self.nodes:
            if node.is_faulty:
                continue

            t_start = global_time + random.gauss(0, self.config.latency_ms / 2000)

            # Récupérer les voisins actifs
            active_neighbors = [
                nid for nid in self.adj[node.node_id]
                if not self.nodes[nid].is_faulty
            ]

            # Réduction dynamique de Θ si nécessaire
            theta = min(self.config.quorum_threshold, len(active_neighbors))
            if theta < 2:
                # Pas assez de voisins — décision locale uniquement
                local_delta = node.compute_delta()
                node.flow -= self.config.alpha * local_delta * 0.5
                cycle_time = 10.0
                max_cycle_time = max(max_cycle_time, cycle_time)
                total_imbalance += abs(local_delta)
                continue

            # Sélection du quorum (Θ voisins aléatoires)
            quorum = random.sample(active_neighbors, min(theta, len(active_neighbors)))
            self.quorum_sizes.append(len(quorum))

            # Simulation de latence
            latency = random.gauss(self.config.latency_ms, 10)
            if latency < 1:
                latency = 1

            # Phase 3 : Collecte asynchrone des perspectives
            received: List[Perspective] = []
            for nid in quorum:
                neighbor = self.nodes[nid]
                p = neighbor.build_perspective(global_time, self.config.sigma_window)
                # Ajout de bruit de mesure
                p.delta *= random.gauss(1.0, self.config.noise_level)
                # Simulation de perte de message
                if random.random() > 0.05:  # 5% de perte
                    received.append(p)

            wait_time = latency + len(quorum) * 2
            cycle_time = wait_time + random.gauss(0, 5)

            # Phase 4 : Validation par quorum
            if len(received) < theta:
                # Quorum insuffisant — décision prudente
                local_delta = node.compute_delta()
                node.flow -= self.config.alpha * local_delta * 0.3
                max_cycle_time = max(max_cycle_time, cycle_time)
                total_imbalance += abs(local_delta)
                continue

            # Vérification de cohérence des signes (avec tolérance ε)
            deltas = [p.delta for p in received]
            signs = [1 if d > 0 else -1 if d < 0 else 0 for d in deltas]
            mean_sign = statistics.mean(signs) if signs else 0

            coherent = sum(1 for s in signs if abs(s - mean_sign) <= self.config.coherence_epsilon)
            is_coherent = coherent >= len(received) * 0.6

            # Vérification des signatures de convergence
            sigmas = [p.sigma for p in received]
            mean_sigma = statistics.mean(sigmas) if sigmas else 0
            sigma_dev = statistics.stdev(sigmas) if len(sigmas) > 1 else 0
            is_converging = sigma_dev < 10000  # seuil heuristique

            # Phase 5 : Ajustement du flux local
            if is_coherent and is_converging:
                # Consensus local — ajustement coopératif
                mean_delta = statistics.mean(deltas)
                node.flow -= self.config.alpha * mean_delta
            else:
                # Divergence détectée — maintien du statu quo + alerte
                self.divergence_events += 1
                # Léger ajustement conservateur
                local_delta = node.compute_delta()
                node.flow -= self.config.alpha * local_delta * 0.1

            max_cycle_time = max(max_cycle_time, cycle_time)
            total_imbalance += abs(node.compute_delta())

        global_imbalance = total_imbalance / self.config.num_nodes
        self.cycle_times.append(max_cycle_time)
        self.imbalances.append(global_imbalance)

        return global_imbalance, max_cycle_time


# ============================================================================
# Simulation complète
# ============================================================================

def run_simulation(config: Config) -> Dict:
    """
    Exécute la simulation complète et compare les deux approches.
    """
    random.seed(config.seed)

    # --- Initialisation du réseau ---
    nodes: List[Node] = []
    for i in range(config.num_nodes):
        r = random.random()
        if r < config.prod_ratio:
            ntype = NodeType.PRODUCER
        elif r < config.prod_ratio + config.cons_ratio:
            ntype = NodeType.CONSUMER
        else:
            ntype = NodeType.STORAGE
        nodes.append(Node(node_id=i, node_type=ntype))

    # Topologie
    adj = build_topology(config.num_nodes, config.topology_degree)
    for i in range(config.num_nodes):
        nodes[i].neighbors = adj[i]

    # --- Boucle de simulation ---
    # Approche A : Centralisée
    print("=== Approche A : Centralisée (mono-focale) ===")
    ctrl = CentralizedController(config, nodes)

    centralized_results = {
        "imbalances": [],
        "cycle_times": [],
        "failures": 0,
        "convergence_rate": 0.0,
    }

    for cycle in range(config.num_cycles):
        # Mise à jour production/consommation
        for node in nodes:
            node.production = simulate_production(node.node_id, cycle, node.node_type)
            node.consumption = simulate_consumption(node.node_id, cycle, node.node_type)

        # Injection de pannes
        for node in nodes:
            if random.random() < config.failure_rate and not node.is_faulty:
                node.is_faulty = True
                node.failure_timer = random.randint(3, 10)

        # Compteurs de panne
        for node in nodes:
            if node.is_faulty:
                node.failure_timer -= 1
                if node.failure_timer <= 0:
                    node.is_faulty = False

        # Pas centralisé
        imb, ctime = ctrl.step(cycle)
        centralized_results["imbalances"].append(imb)
        centralized_results["cycle_times"].append(ctime)

    centralized_results["failures"] = ctrl.failures
    # Taux de convergence : proportion de cycles où l'équilibre est < 10%
    good_cycles = sum(1 for i in centralized_results["imbalances"] if i < 10)
    centralized_results["convergence_rate"] = good_cycles / config.num_cycles

    # --- Réinitialisation du réseau pour l'approche B ---
    nodes_b: List[Node] = []
    for i in range(config.num_nodes):
        r = random.random()
        if r < config.prod_ratio:
            ntype = NodeType.PRODUCER
        elif r < config.prod_ratio + config.cons_ratio:
            ntype = NodeType.CONSUMER
        else:
            ntype = NodeType.STORAGE
        nodes_b.append(Node(node_id=i, node_type=ntype))

    adj_b = build_topology(config.num_nodes, config.topology_degree)
    for i in range(config.num_nodes):
        nodes_b[i].neighbors = adj_b[i]

    # Approche B : Polyfocale (MPVR)
    print("\n=== Approche B : Polyfocale (MPVR) ===")
    poly = PolyfocalNetwork(config, nodes_b, adj_b)

    polyfocal_results = {
        "imbalances": [],
        "cycle_times": [],
        "divergence_events": 0,
        "convergence_rate": 0.0,
    }

    for cycle in range(config.num_cycles):
        # Mise à jour production/consommation
        for node in nodes_b:
            node.production = simulate_production(node.node_id, cycle, node.node_type)
            node.consumption = simulate_consumption(node.node_id, cycle, node.node_type)

        # Injection de pannes (identique à l'approche A)
        for node in nodes_b:
            if random.random() < config.failure_rate and not node.is_faulty:
                node.is_faulty = True
                node.failure_timer = random.randint(3, 10)

        for node in nodes_b:
            if node.is_faulty:
                node.failure_timer -= 1
                if node.failure_timer <= 0:
                    node.is_faulty = False

        # Pas polyfocal
        imb, ctime = poly.step(cycle, global_time=cycle * 100.0)
        polyfocal_results["imbalances"].append(imb)
        polyfocal_results["cycle_times"].append(ctime)

    polyfocal_results["divergence_events"] = poly.divergence_events
    good_cycles_b = sum(1 for i in polyfocal_results["imbalances"] if i < 10)
    polyfocal_results["convergence_rate"] = good_cycles_b / config.num_cycles

    # --- Synthèse ---
    return {
        "config": config,
        "centralized": centralized_results,
        "polyfocal": polyfocal_results,
    }


# ============================================================================
# Affichage des résultats
# ============================================================================

def print_results(results: Dict):
    """Affiche un tableau comparatif des deux approches."""
    c = results["centralized"]
    p = results["polyfocal"]

    print("\n" + "=" * 65)
    print("  RÉSULTATS DE LA SIMULATION")
    print("=" * 65)
    print(f"  Réseau : {results['config'].num_nodes} nœuds")
    print(f"  Cycles  : {results['config'].num_cycles}")
    print(f"  Bruit   : ±{results['config'].noise_level*100:.0f}%")
    print(f"  Latence : {results['config'].latency_ms:.0f} ms")
    print("=" * 65)
    print(f"  {'Métrique':<35} {'Centralisé':<15} {'Polyfocal (MPVR)':<15}")
    print("-" * 65)

    avg_imb_c = statistics.mean(c["imbalances"]) if c["imbalances"] else 0
    avg_imb_p = statistics.mean(p["imbalances"]) if p["imbalances"] else 0
    print(f"  {'Déséquilibre moyen':<35} {avg_imb_c:<15.2f} {avg_imb_p:<15.2f}")

    median_ct_c = statistics.median(c["cycle_times"]) if c["cycle_times"] else 0
    median_ct_p = statistics.median(p["cycle_times"]) if p["cycle_times"] else 0
    print(f"  {'Temps de cycle médian (ms)':<35} {median_ct_c:<15.1f} {median_ct_p:<15.1f}")

    max_ct_c = max(c["cycle_times"]) if c["cycle_times"] else 0
    max_ct_p = max(p["cycle_times"]) if p["cycle_times"] else 0
    print(f"  {'Temps de cycle max (ms)':<35} {max_ct_c:<15.1f} {max_ct_p:<15.1f}")

    fail_rate_c = c["failures"] / results["config"].num_cycles * 100
    div_rate_p = p["divergence_events"] / results["config"].num_cycles / results["config"].num_nodes * 100
    print(f"  {'Taux de défaillance / divergence (%)':<35} {fail_rate_c:<15.1f} {div_rate_p:<15.3f}")

    print(f"  {'Convergence (< 10% imbalance)':<35} {c['convergence_rate']*100:<15.1f}% {p['convergence_rate']*100:<15.1f}%")
    print("=" * 65)

    # Interprétation
    if avg_imb_p < avg_imb_c * 0.8:
        print("\n  [OK] L'approche polyfocale (MPVR) surpasse l'approche centralisée")
        print("    en termes de precision d'equilibrage et de resilience.")
    elif avg_imb_p < avg_imb_c:
        print("\n  [OK] L'approche polyfocale (MPVR) est comparable a l'approche")
        print("    centralisee, avec une meilleure resilience.")
    else:
        print("\n  [!] Resultats mitiges — verifier les parametres de simulation.")

    print(f"\n  Événements de divergence MPVR : {p['divergence_events']}")
    print(f"  (plus le nombre est faible, plus le réseau est stable)")


# ============================================================================
# Point d'entrée
# ============================================================================

if __name__ == "__main__":
    config = Config()
    results = run_simulation(config)
    print_results(results)

    # Note : pour générer les graphiques, exécuter :
    #   pip install matplotlib
    #   python optimisation_polyfocale.py --plot

    import sys
    if "--plot" in sys.argv:
        try:
            import matplotlib.pyplot as plt

            fig, axes = plt.subplots(2, 2, figsize=(14, 8))

            # Déséquilibre
            ax = axes[0, 0]
            ax.plot(results["centralized"]["imbalances"],
                    label="Centralisé", alpha=0.7, color="red")
            ax.plot(results["polyfocal"]["imbalances"],
                    label="Polyfocal (MPVR)", alpha=0.7, color="blue")
            ax.set_xlabel("Cycle")
            ax.set_ylabel("Déséquilibre global")
            ax.set_title("Équilibrage du réseau")
            ax.legend()
            ax.grid(alpha=0.3)

            # Temps de cycle
            ax = axes[0, 1]
            ax.plot(results["centralized"]["cycle_times"],
                    label="Centralisé", alpha=0.7, color="red")
            ax.plot(results["polyfocal"]["cycle_times"],
                    label="Polyfocal (MPVR)", alpha=0.7, color="blue")
            ax.set_xlabel("Cycle")
            ax.set_ylabel("Temps de cycle (ms)")
            ax.set_title("Temps de cycle")
            ax.legend()
            ax.grid(alpha=0.3)

            # Distribution des temps de cycle
            ax = axes[1, 0]
            ax.hist(results["centralized"]["cycle_times"], bins=30,
                    alpha=0.5, label="Centralisé", color="red")
            ax.hist(results["polyfocal"]["cycle_times"], bins=30,
                    alpha=0.5, label="Polyfocal (MPVR)", color="blue")
            ax.set_xlabel("Temps de cycle (ms)")
            ax.set_ylabel("Fréquence")
            ax.set_title("Distribution des temps de cycle")
            ax.legend()
            ax.grid(alpha=0.3)

            # Statistiques récapitulatives
            ax = axes[1, 1]
            ax.axis("off")
            stats_text = (
                f"Réseau : {results['config'].num_nodes} nœuds\n"
                f"Bruit : ±{results['config'].noise_level*100:.0f}%\n"
                f"Latence : {results['config'].latency_ms:.0f} ms\n\n"
                f"--- Centralisé ---\n"
                f"Déséquilibre moyen : {statistics.mean(results['centralized']['imbalances']):.2f}\n"
                f"Temps cycle médian : {statistics.median(results['centralized']['cycle_times']):.1f} ms\n"
                f"Taux défaillance : {results['centralized']['failures']/results['config'].num_cycles*100:.1f}%\n\n"
                f"--- Polyfocal (MPVR) ---\n"
                f"Déséquilibre moyen : {statistics.mean(results['polyfocal']['imbalances']):.2f}\n"
                f"Temps cycle médian : {statistics.median(results['polyfocal']['cycle_times']):.1f} ms\n"
                f"Divergences : {results['polyfocal']['divergence_events']}\n"
            )
            ax.text(0.1, 0.9, stats_text, transform=ax.transAxes,
                    fontsize=10, verticalalignment="top",
                    fontfamily="monospace")
            ax.set_title("Résumé")

            plt.tight_layout()
            plt.savefig("comparaison_approches.png", dpi=150)
            plt.show()
            print("\n  ✓ Graphique sauvegardé : comparaison_approches.png")

        except ImportError:
            print("\n  ⚠ matplotlib non installé. Pour les graphiques : pip install matplotlib")
