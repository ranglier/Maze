// Généré par « cd backend && python -m maze.entries > ../frontend/src/entry-fields.generated.js » depuis
// backend/maze/entries.py (registre des champs, audit M23) : ne pas modifier à la main (vérifié par tests.test_registry).
export const ENTRY_REGISTRY = {
  "npc": [
    {
      "key": "name",
      "label": "Nom",
      "type": "name",
      "group": "identity",
      "secret": false,
      "history": true,
      "comment": false
    },
    {
      "key": "title",
      "label": "Fonction",
      "type": "short",
      "group": "identity",
      "secret": false,
      "history": true,
      "comment": false
    },
    {
      "key": "status",
      "label": "Statut",
      "type": "status",
      "group": "identity",
      "secret": false,
      "history": true,
      "comment": false
    },
    {
      "key": "people",
      "label": "Peuple",
      "type": "short",
      "group": "legacy",
      "secret": false,
      "history": true,
      "comment": false
    },
    {
      "key": "age",
      "label": "Âge",
      "type": "short",
      "group": "legacy",
      "secret": false,
      "history": true,
      "comment": false
    },
    {
      "key": "location",
      "label": "Lieu (ancien champ)",
      "type": "short",
      "group": "legacy",
      "secret": false,
      "history": true,
      "comment": false
    },
    {
      "key": "summary",
      "label": "Résumé",
      "type": "summary",
      "group": "summary",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "appearance",
      "label": "Apparence",
      "type": "long",
      "group": "section",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "personality",
      "label": "Personnalité",
      "type": "long",
      "group": "section",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "motivations",
      "label": "Motivations",
      "type": "long",
      "group": "section",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "secrets",
      "label": "Secret",
      "type": "long",
      "group": "section",
      "secret": true,
      "history": true,
      "comment": true
    },
    {
      "key": "notes",
      "label": "Notes",
      "type": "long",
      "group": "section",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "tags",
      "label": "Étiquettes",
      "type": "tags",
      "group": "tags",
      "secret": false,
      "history": true,
      "comment": false
    }
  ],
  "faction": [
    {
      "key": "name",
      "label": "Nom",
      "type": "name",
      "group": "identity",
      "secret": false,
      "history": true,
      "comment": false
    },
    {
      "key": "kind",
      "label": "Type",
      "type": "short",
      "group": "identity",
      "secret": false,
      "history": true,
      "comment": false
    },
    {
      "key": "color",
      "label": "Couleur",
      "type": "color",
      "group": "identity",
      "secret": false,
      "history": true,
      "comment": false
    },
    {
      "key": "summary",
      "label": "Résumé",
      "type": "summary",
      "group": "summary",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "description",
      "label": "Description",
      "type": "long",
      "group": "section",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "goals",
      "label": "Objectifs",
      "type": "long",
      "group": "section",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "resources",
      "label": "Ressources",
      "type": "long",
      "group": "section",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "secrets",
      "label": "Secret",
      "type": "long",
      "group": "section",
      "secret": true,
      "history": true,
      "comment": true
    },
    {
      "key": "notes",
      "label": "Notes",
      "type": "long",
      "group": "section",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "tags",
      "label": "Étiquettes",
      "type": "tags",
      "group": "tags",
      "secret": false,
      "history": true,
      "comment": false
    }
  ],
  "place": [
    {
      "key": "name",
      "label": "Nom",
      "type": "name",
      "group": "identity",
      "secret": false,
      "history": true,
      "comment": false
    },
    {
      "key": "kind",
      "label": "Type",
      "type": "place_kind",
      "group": "identity",
      "secret": false,
      "history": true,
      "comment": false
    },
    {
      "key": "parent_id",
      "label": "Rangé dans",
      "type": "parent",
      "group": "identity",
      "secret": false,
      "history": false,
      "comment": false
    },
    {
      "key": "summary",
      "label": "Résumé",
      "type": "summary",
      "group": "summary",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "ambiance",
      "label": "Accroche",
      "type": "long",
      "group": "section",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "description",
      "label": "Description",
      "type": "long",
      "group": "section",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "secrets",
      "label": "Secret",
      "type": "long",
      "group": "section",
      "secret": true,
      "history": true,
      "comment": true
    },
    {
      "key": "notes",
      "label": "Notes",
      "type": "long",
      "group": "section",
      "secret": false,
      "history": true,
      "comment": true
    },
    {
      "key": "tags",
      "label": "Étiquettes",
      "type": "tags",
      "group": "tags",
      "secret": false,
      "history": true,
      "comment": false
    }
  ]
};
