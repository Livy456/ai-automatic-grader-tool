"use client"
import { Form, useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { CourseSchema } from "../schemas/courses"
import { z } from "zod"
import { FormField, FormItem, FormLabel } from "@/components/ui/form"
import { RequiredLabelIcon } from "@/components/RequiredLabelIcon"

export function CourseForm()
{
    const form = useForm<z.infer<typeof CourseSchema>>({
        resolver: zodResolver(CourseSchema),
        defaultValues: {
            name: "",
            description: ""
        }
    })

    function onSubmit(){
    
    }

    return (<Form {...form}>
        <form 
            onSubmit={form.handleSubmit(onSubmit)} 
            className="flex gap-6 flex-col">
            <FormField control={form.control}
                name="name"
                render={ ({ field }) => (
                    <FormItem>
                        <FormLabel>
                            <RequiredLabelIcon/>
                            Name
                        </FormLabel>
                    </FormItem>
                )}>
                
                </FormField>
        </form>
    </Form>
    )
}