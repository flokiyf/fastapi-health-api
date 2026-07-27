# AI SafeGuard MCP Gateway

Passerelle distante FastMCP déployable sur Cloudflare Workers. Elle expose exactement un outil
MCP, `ai_gateway`, et conserve un journal d’audit persistant de chaque message MCP dans un Durable
Object SQLite.

## Endpoints

| URL | Usage |
| --- | --- |
| `GET /health` | État du service |
| `/mcp` | Endpoint MCP Streamable HTTP |
| `GET /audit/events` | Derniers événements d’audit (protégé) |
| `GET /audit/events/{id}` | Détail d’un événement (protégé) |

## Fonctionnement de l’outil unique

L’outil `ai_gateway` accepte deux actions :

- `discover` : récupère les outils des serveurs MCP configurés ;
- `execute` : appelle un outil distant par son nom et journalise la requête, les paramètres, la
  réponse, l’erreur éventuelle et la durée.

Les URL distantes ne sont jamais fournies par l’IA. Elles viennent uniquement de la configuration
administrateur `MCP_UPSTREAMS`, ce qui évite de transformer le serveur en proxy HTTP arbitraire.

## Configuration locale

Copier `.dev.vars.example` vers `.dev.vars`, puis remplacer toutes les valeurs. Ce fichier est
ignoré par Git.

```dotenv
MCP_API_KEY=une-cle-longue-et-aleatoire
AUDIT_API_KEY=une-autre-cle-longue-et-aleatoire
MCP_UPSTREAMS={"apple":{"url":"https://apple.example.com/mcp","token_env":"APPLE_MCP_TOKEN","allowed_tools":["calendar_list_events"]}}
APPLE_MCP_TOKEN=secret-du-serveur-apple
```

Chaque entrée de `MCP_UPSTREAMS` accepte :

- `url` : endpoint MCP HTTP distant obligatoire ;
- `token_env` : nom du secret contenant son jeton Bearer ;
- `allowed_tools` : liste facultative des seuls outils autorisés ;
- `timeout_seconds` : délai facultatif, entre 1 et 120 secondes.

Un serveur MCP Apple uniquement local en `stdio` ne peut pas être joint depuis Cloudflare. Il faut
soit l’exposer comme MCP HTTP sécurisé, soit exécuter cette passerelle localement sur la même
machine.

## Installation et tests

```bash
uv sync
pnpm install
uv run ruff check .
uv run pytest
```

## Lancement et déploiement

Les commandes existantes restent inchangées :

```bash
uv run pywrangler dev
uv run pywrangler deploy
```

Dans Cloudflare, créer les secrets/variables d’exécution `MCP_API_KEY`, `AUDIT_API_KEY`,
`MCP_UPSTREAMS` et les éventuels tokens référencés par `token_env`. Ne jamais placer de vrai secret
dans `wrangler.toml` ou dans Git.

## Connexion d’une IA

Configurer un serveur MCP distant avec :

- URL : `https://fastapi-health-api.flokiyf.workers.dev/mcp`
- Transport : Streamable HTTP
- En-tête : `Authorization: Bearer <MCP_API_KEY>`

Instruction recommandée pour l’agent :

> Pour toute action externe, utilise uniquement `ai_gateway`. Commence par `action="discover"`,
> puis utilise `action="execute"`. Transmets la demande originale dans `query` et l’identifiant de
> conversation dans `trace`. N’appelle jamais directement un autre outil.

Pour une couverture stricte, retirer les connexions directes de l’IA vers les serveurs en aval. Si
l’IA conserve un accès direct à un outil, cet appel peut contourner la passerelle et ne sera pas
visible dans l’audit.

## Consultation de l’audit

```bash
curl -H "Authorization: Bearer <AUDIT_API_KEY>" \
  "https://fastapi-health-api.flokiyf.workers.dev/audit/events?limit=50"
```

Filtres facultatifs : `method=tools/call` et `status=success`. Un événement précis est accessible
avec `/audit/events/{id}`. Les champs sensibles tels que mots de passe, cookies, clés API et jetons
sont automatiquement remplacés par `[REDACTED]`; les grandes valeurs sont tronquées.

Le serveur voit tout le trafic qui le traverse, mais pas les appels effectués directement ailleurs,
ni le raisonnement interne caché du modèle.
