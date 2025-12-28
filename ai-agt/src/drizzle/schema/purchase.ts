import {
    pgTable,
    integer,
    jsonb,
    timestamp,
    text,
    uuid,
} from "drizzle-orm/pg-core";
import { relations } from "drizzle-orm";
import { id, createdAt, updatedAt } from "../schemaHelpers";
import { UserTable } from "./user";
import { ProductTable } from "./product";

export const PurchaseTable = pgTable("purchases", {
    id,
    pricePaidInCents: integer().notNull(),
    productDetails: jsonb()
    .notNull()
    .$type<{ name: string; description: string; imageUrl: string }>(),
    userId: uuid()
    .notNull()
    .references(() => UserTable.id, { onDelete: "restrict" }),
    productId: uuid()
    .notNull()
    .references(() => ProductTable.id, { onDelete: "restrict" }),
    stripeSessionId: text().notNull().unique(), // payment system id
    refundedAt: timestamp({ withTimezone: true }),
    updatedAt,
    createdAt
})

export const PurchaseRelationships = relations(PurchaseTable, ({ one }) => ({
    user: one(UserTable, {
        fields: [PurchaseTable.userId],
        references: [UserTable.id],
    }),
    product: one(ProductTable, {
        fields: [PurchaseTable.productId],
        references: [ProductTable.id],
    }),
}))