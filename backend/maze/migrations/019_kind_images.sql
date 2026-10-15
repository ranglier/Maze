-- Image importée comme icône d'un type de lieu (symboles d'une légende de carte : monde-ruche, monde-forge…),
-- à la place de l'icône intégrée ; vide = icône intégrée (colonne icon).
ALTER TABLE place_kinds ADD COLUMN image TEXT NOT NULL DEFAULT '';
-- Style des épingles d'une carte interactive : dot (pastille), icon (icône du type), hidden (discrète :
-- zone cliquable posée sur le symbole déjà imprimé sur le fond).
ALTER TABLE maps ADD COLUMN pin_style TEXT NOT NULL DEFAULT 'dot' CHECK (pin_style IN ('dot', 'icon', 'hidden'));
